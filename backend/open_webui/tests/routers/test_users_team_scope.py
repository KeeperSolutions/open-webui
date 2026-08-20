"""G-A4 - scoping the directory routes to one team.

⚠️ The biggest stake in the ticket. `GET /users/all` passed NO filter at all, so
the difference between correct and catastrophic is a single argument: omit it and
every logged-in account receives every user on the instance.

Both routes moved from `get_admin_user` to `get_verified_user`, so each is
exercised SEPARATELY on every branch - a mutation that removes one guard must not
be caught by the other route's test.
"""

import asyncio
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

sys.modules.setdefault("stripe", MagicMock())

from open_webui.models.users import UserModel
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


def _run(which, caller, team_id=None, team=None, members=None, accounts=None):
    """Call one directory route and report the filter it handed the model."""
    get_users_mock = AsyncMock(return_value=dict(DIRECTORY))
    with patch(TEAMS, AsyncMock(return_value=team)), patch(
        MEMBERS, AsyncMock(return_value=list(TEAM_MEMBERS if members is None else members))
    ), patch(
        USER_IDS, AsyncMock(return_value=list(TEAM_ACCOUNTS if accounts is None else accounts))
    ), patch(GET_USERS, get_users_mock), patch(GROUPS, AsyncMock(return_value={})):
        if which == "paged":
            coro = get_users(
                request=_request(), query=None, order_by=None, direction=None,
                page=1, team_id=team_id, user=caller, db=None,
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
# The gate that moved from the dependency into the body
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
    """⚠️ Asserts on the mock: `pytest.raises` alone cannot tell WHERE it raised."""
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
    """⚠️ The mock outlives the exception, so the second half of the name is asserted.

    `_run` returns nothing when it raises, so the assertion has to hold the mock
    itself - otherwise this test checks the status code and quietly promises
    something it never looked at.
    """
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
    """⚠️ `models/users.py:448` arms its guard only for `isinstance(..., list)`."""
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
