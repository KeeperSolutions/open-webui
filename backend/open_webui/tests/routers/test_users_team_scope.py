"""Tests that the directory routes `GET /users/` and `GET /users/all` are scoped to one team.

Unscoped, `GET /users/all` passes no filter, so a missing scope hands every
logged-in account the whole user directory. Both routes depend on
`get_verified_user` and authorise in the body, so each route is tested
separately on every branch.
"""

import asyncio
import datetime
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

sys.modules.setdefault("stripe", MagicMock())

from open_webui.models.users import UserModel, UserSettings
from open_webui.utils.team_groups import TeamOwnership
from open_webui.routers import users as route_mod
from open_webui.routers.users import get_all_users, get_users
from open_webui.utils.auth import get_admin_user, get_verified_user

TEAMS = "open_webui.models.billing.Teams.get_by_id"
MEMBERS = "open_webui.models.billing.TeamMembers.get_by_team_id"
USER_IDS = "open_webui.models.users.Users.get_users_by_user_ids"
GET_USERS = "open_webui.routers.users.Users.get_users"
GROUPS = "open_webui.routers.users.Groups.get_groups_by_member_ids"


def _caller(role="user", user_id="u1"):
    return SimpleNamespace(role=role, id=user_id)


def _team(owner_user_id, team_id="T1"):
    return SimpleNamespace(id=team_id, owner_user_id=owner_user_id)


def _member(user_id):
    return SimpleNamespace(user_id=user_id)


def _account(user_id, email):
    now = int(time.time())
    return UserModel(
        id=user_id, email=email, name=user_id, role="user",
        created_at=now, updated_at=now, last_active_at=now,
    )


TEAM_MEMBERS = [_member("u1"), _member("u2")]
TEAM_ACCOUNTS = [_account("u1", "ana@x.com"), _account("u2", "bojan@x.com")]
DIRECTORY = {"users": TEAM_ACCOUNTS, "total": 2}


def _request():
    config = SimpleNamespace(USER_PERMISSIONS={})
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=config)))


def _group(group_id, masking=True):
    """Only the two fields the route reads off a group."""
    return SimpleNamespace(id=group_id, permissions={"chat": {"pii_masking_enforced": masking}})


def _run(which, caller, team_id=None, team=None, members=None, accounts=None, page=1,
         groups=None, directory=None):
    """Call one directory route and report the filter it handed the model.

    `page` lets tests check that the scope fields are the same on every page.
    `directory` replaces the listed accounts, for tests about the rows themselves.
    """
    get_users_mock = AsyncMock(return_value=dict(directory if directory is not None else DIRECTORY))
    with patch(TEAMS, AsyncMock(return_value=team)), patch(
        MEMBERS, AsyncMock(return_value=list(TEAM_MEMBERS if members is None else members))
    ), patch(
        USER_IDS, AsyncMock(return_value=list(TEAM_ACCOUNTS if accounts is None else accounts))
    ), patch(GET_USERS, get_users_mock), patch(
        GROUPS, AsyncMock(return_value=groups if groups is not None else {})
    ):
        if which == "paged":
            coro = get_users(
                request=_request(), query=None, order_by=None, direction=None,
                page=page, team_id=team_id, user=caller, db=None,
            )
        else:
            coro = get_all_users(team_id=team_id, user=caller, db=None)
        result = asyncio.run(coro)
    return result, get_users_mock


def _filter_of(mock):
    assert mock.await_count == 1, f"expected exactly one directory read, got {mock.await_count}"
    return mock.await_args.kwargs.get("filter")


ROUTES = ("paged", "all")


# ---------------------------------------------------------------------------
# Authorisation in the route body
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/all"])
def test_directory_routes_now_depend_on_get_verified_user(path):
    calls = []
    for route in route_mod.router.routes:
        if getattr(route, "path", None) == path:
            calls += [d.call for d in route.dependant.dependencies if d.name == "user"]
    assert calls == [get_verified_user], path
    assert get_admin_user not in calls


@pytest.mark.parametrize("which", ROUTES)
def test_non_admin_without_team_id_is_refused_on_each_route(which):
    with pytest.raises(HTTPException) as e:
        _run(which, _caller("user", "u1"))
    assert e.value.status_code == 401


@pytest.mark.parametrize("which", ROUTES)
def test_refused_before_the_directory_is_ever_read(which):
    """Asserts on the mock, because `pytest.raises` alone cannot show where it raised."""
    get_users_mock = AsyncMock(return_value=dict(DIRECTORY))
    with patch(GET_USERS, get_users_mock), patch(GROUPS, AsyncMock(return_value={})):
        with pytest.raises(HTTPException):
            if which == "paged":
                asyncio.run(get_users(request=_request(), query=None, order_by=None,
                                      direction=None, page=1, team_id=None,
                                      user=_caller("user", "u1"), db=None))
            else:
                asyncio.run(get_all_users(team_id=None, user=_caller("user", "u1"), db=None))
    get_users_mock.assert_not_awaited()


@pytest.mark.parametrize("which", ROUTES)
def test_owner_of_another_team_is_refused_on_each_route(which):
    with pytest.raises(HTTPException) as e:
        _run(which, _caller("user", "u1"), team_id="T2",
             team=_team("u2", team_id="T2"))
    assert e.value.status_code == 401


@pytest.mark.parametrize("which", ROUTES)
def test_empty_team_is_refused_and_the_directory_is_not_read(which):
    """Holds the directory mock itself, because `_run` returns nothing when it raises."""
    get_users_mock = AsyncMock(return_value=dict(DIRECTORY))
    with patch(TEAMS, AsyncMock(return_value=_team("u1"))), patch(
        MEMBERS, AsyncMock(return_value=[])
    ), patch(USER_IDS, AsyncMock(return_value=[])), patch(
        GET_USERS, get_users_mock
    ), patch(GROUPS, AsyncMock(return_value={})):
        with pytest.raises(HTTPException) as e:
            if which == "paged":
                asyncio.run(get_users(request=_request(), query=None, order_by=None,
                                      direction=None, page=1, team_id="T1",
                                      user=_caller("user", "u1"), db=None))
            else:
                asyncio.run(get_all_users(team_id="T1", user=_caller("user", "u1"),
                                          db=None))
    assert e.value.status_code == 401
    get_users_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# The filter each route hands the model
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("which", ROUTES)
def test_admin_without_team_id_keeps_todays_filter(which):
    _, mock = _run(which, _caller("admin", "adm"))
    sent = _filter_of(mock)
    if which == "all":
        assert sent is None, "the unscoped listing must pass no filter, exactly as before"
    else:
        assert "user_ids" not in sent and "group_ids" not in sent


@pytest.mark.parametrize("which", ROUTES)
def test_owner_scopes_the_directory_to_their_team(which):
    _, mock = _run(which, _caller("user", "u1"), team_id="T1", team=_team("u1"))
    sent = _filter_of(mock)
    assert "user_ids" in sent, "the scope never reached the model: the directory is unfiltered"
    assert "group_ids" in sent, "group_ids is missing, so the empty-scope guard is disarmed"
    assert sent["user_ids"] == ["u1", "u2"]
    assert sent["group_ids"] == []


@pytest.mark.parametrize("which", ROUTES)
def test_both_filter_keys_are_lists(which):
    """`Users.get_users` applies its empty-filter guard only when both keys are lists."""
    _, mock = _run(which, _caller("user", "u1"), team_id="T1", team=_team("u1"))
    sent = _filter_of(mock)
    assert {"user_ids", "group_ids"} <= set(sent), f"filter is missing keys: {sorted(sent)}"
    assert isinstance(sent["user_ids"], list)
    assert isinstance(sent["group_ids"], list)


@pytest.mark.parametrize("which", ROUTES)
def test_admin_may_scope_to_any_team(which):
    _, mock = _run(which, _caller("admin", "adm"), team_id="T1",
                   team=_team("someone-else"))
    assert _filter_of(mock)["user_ids"] == ["u1", "u2"]


@pytest.mark.parametrize("which", ROUTES)
def test_a_member_listed_twice_appears_once_in_the_filter(which):
    _, mock = _run(which, _caller("user", "u1"), team_id="T1", team=_team("u1"),
                   members=[_member("u1"), _member("u1"), _member("u2")])
    assert _filter_of(mock)["user_ids"] == ["u1", "u2"]


@pytest.mark.parametrize("which", ROUTES)
def test_the_paged_route_keeps_its_ordering_filter_alongside_the_scope(which):
    """Scoping is merged into the existing filter, it does not replace it."""
    if which == "all":
        pytest.skip("the unpaginated route has no ordering filter to preserve")
    get_users_mock = AsyncMock(return_value=dict(DIRECTORY))
    with patch(TEAMS, AsyncMock(return_value=_team("u1"))), patch(
        MEMBERS, AsyncMock(return_value=list(TEAM_MEMBERS))
    ), patch(USER_IDS, AsyncMock(return_value=list(TEAM_ACCOUNTS))), patch(
        GET_USERS, get_users_mock
    ), patch(GROUPS, AsyncMock(return_value={})):
        asyncio.run(get_users(request=_request(), query="ana", order_by="name",
                              direction="asc", page=1, team_id="T1",
                              user=_caller("user", "u1"), db=None))
    sent = _filter_of(get_users_mock)
    assert sent["query"] == "ana" and sent["order_by"] == "name"
    assert sent["user_ids"] == ["u1", "u2"]


# ---------------------------------------------------------------------------
# `team_group_id`: the team's own policy group
# ---------------------------------------------------------------------------


class TestTeamGroupIdInTheResponse:
    """The directory reports the addressed team's policy group id, and no other.

    The dashboard uses it to label masking as team policy.
    """

    def test_the_paged_route_reports_it(self):
        with patch(
            "open_webui.utils.team_groups.ensure_team_pii_group",
            AsyncMock(return_value="g-team"),
        ):
            result, _ = _run("paged", _caller(user_id="owner"), team_id="T1", team=_team("owner"))
        assert result["team_group_id"] == "g-team"

    def test_the_instance_wide_view_reports_none(self):
        """No team addressed, so there is no team policy to attribute masking to."""
        result, _ = _run("paged", _caller(role="admin"))
        assert result["team_group_id"] is None

    def test_a_team_with_no_group_yet_reports_none(self):
        """A team without a policy group is a supported state with a frontend fallback."""
        with patch(
            "open_webui.utils.team_groups.ensure_team_pii_group", AsyncMock(return_value=None)
        ):
            result, _ = _run("paged", _caller(user_id="owner"), team_id="T1", team=_team("owner"))
        assert result["team_group_id"] is None


# ---------------------------------------------------------------------------
# `may_manage_team_policy`: whether the viewer may manage that group
# ---------------------------------------------------------------------------


class TestMayManageTeamPolicyInTheResponse:
    """The permission is computed on the server and reported in the response.

    The client cannot derive it: a `team_id` in the address selects a scope but
    grants nothing, and the frontend cannot check who owns a team.
    """

    @staticmethod
    def _with_owner(owner_user_id, group_id="g-team"):
        """Patch both halves the flag rests on: the group's existence and its team."""
        return (
            patch(
                "open_webui.utils.team_groups.ensure_team_pii_group",
                AsyncMock(return_value=group_id),
            ),
            patch(
                "open_webui.utils.team_groups.team_ownership_of_group",
                AsyncMock(return_value=TeamOwnership(team_id="T1", owner_user_id=owner_user_id)),
            ),
        )

    def test_the_owner_of_the_addressed_team_may(self):
        ensure, ownership = self._with_owner("owner")
        with ensure, ownership:
            result, _ = _run("paged", _caller(user_id="owner"), team_id="T1", team=_team("owner"))
        assert result["may_manage_team_policy"] is True

    def test_an_admin_may_on_somebody_elses_team(self):
        """Admins may manage any team's group, without an ownership lookup."""
        ensure, ownership = self._with_owner("someone-else")
        with ensure, ownership:
            result, _ = _run("paged", _caller(role="admin"), team_id="T1", team=_team("someone-else"))
        assert result["may_manage_team_policy"] is True

    def test_the_instance_wide_view_reports_false(self):
        """No team addressed, so there is no team policy to govern."""
        result, _ = _run("paged", _caller(role="admin"))
        assert result["may_manage_team_policy"] is False

    def test_a_team_with_no_group_yet_reports_false(self):
        """No group means nothing to manage, for admins as well as owners."""
        with patch(
            "open_webui.utils.team_groups.ensure_team_pii_group", AsyncMock(return_value=None)
        ):
            owner, _ = _run("paged", _caller(user_id="owner"), team_id="T1", team=_team("owner"))
            admin, _ = _run("paged", _caller(role="admin"), team_id="T1", team=_team("owner"))
        assert owner["may_manage_team_policy"] is False
        assert admin["may_manage_team_policy"] is False


    def test_the_route_asks_the_permission_rather_than_inferring_it(self):
        """The route calls `may_manage_team_policy` instead of inferring it from the scope.

        Only admins and owners can read a scoped view today, so inferring it would
        pass every case above but break once the read audience widens.
        """
        sentinel = AsyncMock(return_value=False)
        with patch(
            "open_webui.utils.team_groups.ensure_team_pii_group", AsyncMock(return_value="g-team")
        ), patch("open_webui.routers.users.may_manage_team_policy", sentinel):
            result, _ = _run("paged", _caller(user_id="owner"), team_id="T1", team=_team("owner"))

        sentinel.assert_awaited_once()
        assert sentinel.await_args.args[1] == "g-team", "the addressed team's group, not the team id"
        assert result["may_manage_team_policy"] is False, "the route reports what it was told"


class TestTheScopeFieldsAreNotPageProperties:
    """`team_group_id` and `may_manage_team_policy` depend on the scope, not the page.

    A field computed per page would make the owner's controls vanish on later pages.
    """

    def test_both_scope_fields_travel_on_page_two(self):
        ensure, ownership = TestMayManageTeamPolicyInTheResponse._with_owner("owner")
        with ensure, ownership:
            first, _ = _run(
                "paged", _caller(user_id="owner"), team_id="T1", team=_team("owner"), page=1
            )
            second, _ = _run(
                "paged", _caller(user_id="owner"), team_id="T1", team=_team("owner"), page=2
            )

        for field in ("team_group_id", "may_manage_team_policy"):
            assert second[field] == first[field], field
        assert second["team_group_id"] == "g-team"
        assert second["may_manage_team_policy"] is True


# ---------------------------------------------------------------------------
# A group id exposes the group's name, so non-admins get none outside their team
# ---------------------------------------------------------------------------


class TestGroupIdsAreNotHandedToNonAdmins:
    """Non-admins receive no group ids other than their team's policy group.

    `GET /groups/id/{id}/info` requires only a verified user and checks no
    membership, so any group id in this response exposes that group's name,
    description and member count. The rule depends on the viewer's role, not on
    whether the request is scoped.
    """

    TEAM_GROUP = "g-team"
    OTHER = "g-legal"

    def _rows(self, caller, groups):
        with patch(
            "open_webui.utils.team_groups.ensure_team_pii_group",
            AsyncMock(return_value=self.TEAM_GROUP),
        ):
            result, _ = _run(
                "paged", caller, team_id="T1", team=_team("u1"), groups=groups
            )
        return {u.id: u for u in result["users"]}

    def test_a_non_admin_is_told_nothing_about_groups_outside_their_team(self):
        rows = self._rows(
            _caller("user", "u1"),
            {"u1": [_group(self.TEAM_GROUP), _group(self.OTHER)], "u2": [_group(self.OTHER)]},
        )
        assert rows["u1"].pii_policy_group_ids == [self.TEAM_GROUP]
        assert rows["u2"].pii_policy_group_ids == []
        # `group_ids` carries non-policy groups and leaks names just as well.
        assert rows["u1"].group_ids == [] and rows["u2"].group_ids == []

    def test_but_they_are_still_told_THAT_something_else_masks_them(self):
        """Non-admins still learn that another policy masks a member.

        Otherwise the dialog would suggest that removing someone from the team's
        policy lets them turn masking off while another group still enforces it.
        """
        rows = self._rows(
            _caller("user", "u1"),
            {"u1": [_group(self.TEAM_GROUP), _group(self.OTHER)], "u2": [_group(self.OTHER)]},
        )
        assert rows["u1"].masked_by_other_policy is True
        assert rows["u2"].masked_by_other_policy is True

    def test_and_it_is_false_when_only_the_team_masks_them(self):
        rows = self._rows(_caller("user", "u1"), {"u1": [_group(self.TEAM_GROUP)]})
        assert rows["u1"].masked_by_other_policy is False
        assert rows["u2"].masked_by_other_policy is False

    def test_an_admin_keeps_every_id(self):
        rows = self._rows(
            _caller("admin", "adm"),
            {"u1": [_group(self.TEAM_GROUP), _group(self.OTHER)], "u2": [_group(self.OTHER)]},
        )
        assert set(rows["u1"].pii_policy_group_ids) == {self.TEAM_GROUP, self.OTHER}
        assert rows["u2"].pii_policy_group_ids == [self.OTHER]
        assert set(rows["u1"].group_ids) == {self.TEAM_GROUP, self.OTHER}

    def test_a_group_that_does_not_mask_is_not_a_policy_group_for_either_viewer(self):
        groups = {"u1": [_group(self.TEAM_GROUP), _group("g-plain", masking=False)]}
        assert self._rows(_caller("user", "u1"), groups)["u1"].pii_policy_group_ids == [
            self.TEAM_GROUP
        ]
        admin_row = self._rows(_caller("admin", "adm"), groups)["u1"]
        assert admin_row.pii_policy_group_ids == [self.TEAM_GROUP]
        assert set(admin_row.group_ids) == {self.TEAM_GROUP, "g-plain"}


class TestMaskedByOtherPolicyCountsTheInstanceDefault:
    """`masked_by_other_policy` counts the instance-wide default permission.

    Unlike `pii_policy_group_ids`, it asks whether a user stays masked without the
    team's group, and with `USER_PERMISSIONS_CHAT_CHAT_PII_MASKING_ENFORCED` on
    they do.
    """

    def _rows(self, default_permissions, groups):
        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    config=SimpleNamespace(USER_PERMISSIONS=default_permissions)
                )
            )
        )
        with patch(TEAMS, AsyncMock(return_value=_team("u1"))), patch(
            MEMBERS, AsyncMock(return_value=list(TEAM_MEMBERS))
        ), patch(USER_IDS, AsyncMock(return_value=list(TEAM_ACCOUNTS))), patch(
            GET_USERS, AsyncMock(return_value=dict(DIRECTORY))
        ), patch(GROUPS, AsyncMock(return_value=groups)), patch(
            "open_webui.utils.team_groups.ensure_team_pii_group",
            AsyncMock(return_value="g-team"),
        ):
            result = asyncio.run(
                get_users(
                    request=request, query=None, order_by=None, direction=None,
                    page=1, team_id="T1", user=_caller("user", "u1"), db=None,
                )
            )
        return {u.id: u for u in result["users"]}

    def test_the_instance_default_alone_makes_it_true(self):
        rows = self._rows(
            {"chat": {"pii_masking_enforced": True}}, {"u1": [_group("g-team")]}
        )
        assert rows["u1"].masked_by_other_policy is True

    def test_and_off_by_default_it_is_false(self):
        rows = self._rows(
            {"chat": {"pii_masking_enforced": False}}, {"u1": [_group("g-team")]}
        )
        assert rows["u1"].masked_by_other_policy is False


class TestANonAdminReceivesOnlyTheDashboardFields:
    """A team owner's rows carry the dashboard's fields and nothing else.

    The route is open to any verified user, so an unprojected row would hand a
    team owner every profile and authentication field the account model holds.
    """

    TEAM_GROUP = "g-team"

    @staticmethod
    def _profile(user_id, email):
        """An account with every field the projection must drop populated."""
        account = _account(user_id, email)
        account.date_of_birth = datetime.date(1990, 1, 1)
        account.gender = "female"
        account.bio = "private"
        account.timezone = "Europe/Zagreb"
        account.info = {"note": "private"}
        account.oauth = {"oidc": {"sub": "sub-123"}}
        account.scim = {"externalId": "x-1"}
        account.settings = UserSettings(
            ui={
                "system_prompt": "private",
                "pipelines": {
                    "valves": {
                        "pii_filter": {"pii_masking_enabled": False, "note": "private"},
                        "other_pipeline": {"secret": "private"},
                    }
                },
            }
        )
        return account

    def _rows(self, caller):
        accounts = [self._profile("u1", "ana@x.com"), self._profile("u2", "bojan@x.com")]
        with patch(
            "open_webui.utils.team_groups.ensure_team_pii_group",
            AsyncMock(return_value=self.TEAM_GROUP),
        ):
            result, _ = _run(
                "paged",
                caller,
                team_id="T1",
                team=_team("u1"),
                accounts=accounts,
                directory={"users": accounts, "total": 2},
                groups={"u1": [_group(self.TEAM_GROUP)]},
            )
        return {u.id: u for u in result["users"]}

    DROPPED = [
        "date_of_birth",
        "gender",
        "bio",
        "timezone",
        "info",
        "oauth",
        "scim",
        "username",
        "profile_image_url",
        "presence_state",
        "status_message",
        "last_active_at",
        "created_at",
        "updated_at",
    ]

    @pytest.mark.parametrize("field", DROPPED)
    def test_the_row_carries_no_profile_or_authentication_field(self, field):
        row = self._rows(_caller("user", "u1"))["u2"]
        assert not hasattr(row, field), f"{field} reached a non-admin viewer"

    def test_it_still_carries_what_the_dashboard_renders(self):
        row = self._rows(_caller("user", "u1"))["u1"]
        assert (row.id, row.name, row.email, row.role) == ("u1", "u1", "ana@x.com", "user")
        assert row.pii_policy_group_ids == [self.TEAM_GROUP]
        assert row.pii_masking_enforced is True
        assert row.masked_by_other_policy is False

    def test_the_stored_masking_preference_survives_the_projection(self):
        """The masking column reads the user's own stored preference."""
        valves = self._rows(_caller("user", "u1"))["u1"].settings["ui"]["pipelines"]["valves"]
        assert valves == {"pii_filter": {"pii_masking_enabled": False}}

    def test_but_nothing_else_stored_under_settings(self):
        settings = self._rows(_caller("user", "u1"))["u1"].settings
        assert set(settings["ui"]) == {"pipelines"}
        assert "other_pipeline" not in settings["ui"]["pipelines"]["valves"]

    def test_a_user_who_stored_no_preference_gets_an_empty_valve_map(self):
        accounts = [_account("u1", "ana@x.com"), _account("u2", "bojan@x.com")]
        with patch(
            "open_webui.utils.team_groups.ensure_team_pii_group",
            AsyncMock(return_value=self.TEAM_GROUP),
        ):
            result, _ = _run(
                "paged", _caller("user", "u1"), team_id="T1", team=_team("u1"),
                accounts=accounts, directory={"users": accounts, "total": 2}, groups={},
            )
        assert result["users"][0].settings["ui"]["pipelines"]["valves"] == {}

    @pytest.mark.parametrize("field", ["date_of_birth", "oauth", "scim", "last_active_at"])
    def test_an_admin_still_receives_the_whole_account(self, field):
        row = self._rows(_caller("admin", "adm"))["u2"]
        assert hasattr(row, field)


class TestSettingsThatAreNotShapedLikeSettings:
    """Odd stored settings yield no preference instead of failing the page.

    `POST /users/user/settings/update` stores `ui` as a free-form dict, so any
    user can put a string where the masking valves are expected. Raising here
    would answer their team owner's whole directory page with a 500.
    """

    @staticmethod
    def _subject(ui):
        account = _account("u1", "ana@x.com")
        account.settings = UserSettings(ui=ui)
        return account

    @pytest.mark.parametrize(
        "ui",
        [
            {"pipelines": "not-a-dict"},
            {"pipelines": {"valves": "not-a-dict"}},
            {"pipelines": {"valves": [1, 2]}},
            {"pipelines": {"valves": {"pii_filter": "yes"}}},
            {"pipelines": {"valves": {"pii_filter": {"pii_masking_enabled": "yes"}}}},
            {"pipelines": None},
            {},
        ],
    )
    def test_no_preference_is_read_and_nothing_raises(self, ui):
        assert route_mod._masking_valves(self._subject(ui)) == {}

    def test_a_well_formed_preference_is_still_read(self):
        subject = self._subject(
            {"pipelines": {"valves": {"pii_filter": {"pii_masking_enabled": False}}}}
        )
        assert route_mod._masking_valves(subject) == {
            "pii_filter": {"pii_masking_enabled": False}
        }


class TestTheResponseModelKeepsTheTwoRowsApart:
    """The route's response model holds both row types, so it must not mix them.

    A narrowed row validated as the admin model would be a serialisation that
    re-admits the fields the narrow row exists to withhold.
    """

    @staticmethod
    def _admin_row():
        from open_webui.models.users import UserGroupIdsModel

        now = int(time.time())
        return UserGroupIdsModel(
            id="u1", email="ana@x.com", name="Ana", role="user",
            created_at=now, updated_at=now, last_active_at=now,
            date_of_birth=datetime.date(1990, 1, 1), oauth={"oidc": {"sub": "s"}},
        )

    @staticmethod
    def _narrow_row():
        from open_webui.models.users import TeamDirectoryUserModel

        return TeamDirectoryUserModel(id="u2", name="Bojan", email="b@x.com", role="user")

    def _validated(self):
        from open_webui.models.users import UserGroupIdsListResponse

        response = UserGroupIdsListResponse(
            users=[self._admin_row(), self._narrow_row()], total=2
        )
        # Round-tripped through dicts, as FastAPI validates the returned body.
        return UserGroupIdsListResponse(**response.model_dump()).model_dump()['users']

    def test_the_admin_row_keeps_the_whole_account(self):
        assert {'date_of_birth', 'oauth', 'last_active_at'} <= set(self._validated()[0])

    def test_the_narrow_row_gains_no_field(self):
        assert set(self._validated()[1]) == {
            'id', 'name', 'email', 'role', 'group_ids', 'settings',
            'pii_masking_enforced', 'pii_policy_group_ids', 'masked_by_other_policy',
        }
