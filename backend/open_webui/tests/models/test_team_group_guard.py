"""G-B2 — a team's PII group is not editable and not deletable.

Its permissions and its name are derived from the team, so the only honest way to
change either is to change the team. The guard lives in the MODEL, because the
five writers of a group do not share a route: SCIM, LDAP and OAuth all reach
`Groups` directly, and a guard on the admin route would protect one of them.

⚠️ Route and SCIM are separate tests throughout. Both end up in the same model
method, which is exactly why one test cannot stand in for the other: a guard
wrongly placed in the admin route passes the route test and fails nothing else.
The non-overlap IS the proof that the guard sits where it claims to.

⚠️ The guard refuses a CHANGE, never a restatement. OAuth writes a group's own
permissions straight back to it and SCIM resends the current name on every
membership edit; refusing those would break directory sync while protecting
nothing.
"""

import sys
import time
from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

sys.modules.setdefault("stripe", MagicMock())

from fastapi import HTTPException

from open_webui.models.billing import Team
from open_webui.models.groups import Group, GroupMember, GroupUpdateForm


TEAM_GROUP = "g-team-pii"
CUSTOM_GROUP = "g-custom"
ENFORCING = {"chat": {"pii_masking_enforced": True}}
TEAM_GROUP_NAME = "PII — Acme · t1"


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Team.__table__.create, checkfirst=True)
        await conn.run_sync(Group.__table__.create, checkfirst=True)
        # `get_groups` counts members in the same statement, so the table has to
        # exist even when no test in this file adds a member.
        await conn.run_sync(GroupMember.__table__.create, checkfirst=True)

    session = async_sessionmaker(bind=engine, expire_on_commit=False)()
    now = int(time.time())
    session.add_all(
        [
            Team(
                id="t1",
                name="Acme",
                owner_user_id="owner",
                seat_limit=10,
                monthly_credits=0,
                group_id=TEAM_GROUP,
                created_at=now,
                updated_at=now,
            ),
            Group(
                id=TEAM_GROUP,
                user_id="",
                name=TEAM_GROUP_NAME,
                description="Acme's policy",
                data={},
                meta=None,
                permissions=ENFORCING,
                created_at=now,
                updated_at=now,
            ),
            # Enforcing and named like a team group, but no team points at it.
            Group(
                id=CUSTOM_GROUP,
                user_id="admin",
                name="PII — Marketing · deadbeef",
                description="",
                data={},
                meta=None,
                permissions=ENFORCING,
                created_at=now,
                updated_at=now,
            ),
        ]
    )
    await session.commit()
    yield session
    await session.close()
    await engine.dispose()


@pytest_asyncio.fixture
async def groups(db_session):
    """The real `Groups` bound to the in-memory session.

    Both context managers are patched: `models.groups` opens its own, and
    `team_group_kind` reaches for `internal.db`'s. Patching one leaves the other
    talking to the developer's own database — `DATABASE_ENABLE_SESSION_SHARING`
    is off, so a passed session is ignored.
    """
    from open_webui.models import groups as groups_module

    @asynccontextmanager
    async def _ctx(db=None):
        yield db_session

    with patch.object(groups_module, "get_async_db_context", _ctx), patch(
        "open_webui.internal.db.get_async_db_context", _ctx
    ):
        yield groups_module.Groups


async def _stored(db_session, group_id):
    result = await db_session.execute(select(Group).filter_by(id=group_id))
    return result.scalars().first()


def _form(name=TEAM_GROUP_NAME, description="Acme's policy", permissions=None):
    return GroupUpdateForm(name=name, description=description, permissions=permissions)


# ---------------------------------------------------------------------------
# permissions and name are derived, so they are not editable here
# ---------------------------------------------------------------------------


class TestUpdateGuard:
    @pytest.mark.asyncio
    async def test_changing_permissions_is_refused(self, groups, db_session):
        assert await groups.update_group_by_id(
            TEAM_GROUP, _form(permissions={"chat": {"pii_masking_enforced": False}})
        ) is None
        assert (await _stored(db_session, TEAM_GROUP)).permissions == ENFORCING

    @pytest.mark.asyncio
    async def test_changing_the_name_is_refused(self, groups, db_session):
        assert await groups.update_group_by_id(TEAM_GROUP, _form(name="Something else")) is None
        assert (await _stored(db_session, TEAM_GROUP)).name == TEAM_GROUP_NAME

    @pytest.mark.asyncio
    async def test_changing_the_description_goes_through(self, groups, db_session):
        """Only what is derived is frozen. The description is nobody's invariant."""
        assert await groups.update_group_by_id(TEAM_GROUP, _form(description="New blurb")) is not None
        assert (await _stored(db_session, TEAM_GROUP)).description == "New blurb"

    @pytest.mark.asyncio
    async def test_restating_the_same_permissions_goes_through(self, groups, db_session):
        """⚠️ OAuth does exactly this on every sync (`utils/oauth.py:1412`).

        A guard that refused any non-None `permissions` would pass every other
        test in this class and break directory sync for team groups.
        """
        assert await groups.update_group_by_id(TEAM_GROUP, _form(permissions=ENFORCING)) is not None
        assert (await _stored(db_session, TEAM_GROUP)).permissions == ENFORCING

    @pytest.mark.asyncio
    async def test_restating_the_same_name_goes_through(self, groups, db_session):
        """SCIM resends the current name on every membership edit."""
        assert await groups.update_group_by_id(TEAM_GROUP, _form(name=TEAM_GROUP_NAME)) is not None

    @pytest.mark.asyncio
    async def test_a_custom_policy_group_is_untouched_by_this_guard(self, groups, db_session):
        """Enforcing, and named like a team group — but no team points at it."""
        assert await groups.update_group_by_id(
            CUSTOM_GROUP,
            GroupUpdateForm(name="Renamed", description="", permissions={"chat": {}}),
        ) is not None
        assert (await _stored(db_session, CUSTOM_GROUP)).name == "Renamed"


# ---------------------------------------------------------------------------
# deletion — three doors, three tests
# ---------------------------------------------------------------------------


class TestDeleteGuard:
    @pytest.mark.asyncio
    async def test_model_refuses(self, groups, db_session):
        assert await groups.delete_group_by_id(TEAM_GROUP) is False
        assert await _stored(db_session, TEAM_GROUP) is not None

    @pytest.mark.asyncio
    async def test_admin_route_refuses(self, groups, db_session):
        from open_webui.routers import groups as groups_router

        with patch.object(groups_router, "Groups", groups):
            with pytest.raises(HTTPException) as exc:
                await groups_router.delete_group_by_id(
                    id=TEAM_GROUP, user=MagicMock(id="admin", role="admin"), db=db_session
                )
        assert exc.value.status_code == 400
        assert await _stored(db_session, TEAM_GROUP) is not None

    @pytest.mark.asyncio
    async def test_scim_route_refuses(self, groups, db_session):
        """⚠️ Not covered by the route test above — SCIM has its own handler.

        Same model method underneath, which is the point: if the guard ever moves
        into the admin route, this is the test that notices.
        """
        from open_webui.routers import scim as scim_router

        with patch.object(scim_router, "Groups", groups):
            with pytest.raises(HTTPException) as exc:
                await scim_router.delete_group(
                    group_id=TEAM_GROUP, request=MagicMock(), _=True, db=db_session
                )
        assert exc.value.status_code == 500
        assert await _stored(db_session, TEAM_GROUP) is not None

    @pytest.mark.asyncio
    async def test_a_custom_group_still_deletes(self, groups, db_session):
        assert await groups.delete_group_by_id(CUSTOM_GROUP) is True
        assert await _stored(db_session, CUSTOM_GROUP) is None


@pytest.mark.asyncio
async def test_the_guard_asks_team_group_kind_rather_than_querying_itself(groups):
    """One reader of `teams.group_id`, enforced structurally in G-B1.

    Patching the shared classifier must be enough to change this guard's mind. If
    it ever grows its own query, this test keeps passing while the structural test
    in `test_team_groups.py` starts failing — the two together are what pin it.
    """
    async def _says_not_a_team_group(group_id, db=None):
        return None

    with patch("open_webui.utils.team_groups.team_group_kind", _says_not_a_team_group):
        assert await groups.delete_group_by_id(TEAM_GROUP) is True


class TestIsTeamGroupFlag:
    """`GET /groups/` reports which groups a team owns, so the UI can exclude them.

    ⚠️ A flag rather than the team id: the only question the reader has is "may
    this be an enforce destination". And it is REPORTED, not filtered — the admin
    group screen must keep listing team groups.
    """

    @pytest.mark.asyncio
    async def test_a_team_group_is_flagged(self, groups):
        listed = {g.id: g for g in await groups.get_groups({})}
        assert listed[TEAM_GROUP].is_team_group is True

    @pytest.mark.asyncio
    async def test_a_custom_group_is_not(self, groups):
        listed = {g.id: g for g in await groups.get_groups({})}
        assert listed[CUSTOM_GROUP].is_team_group is False

    @pytest.mark.asyncio
    async def test_team_groups_are_still_listed(self, groups):
        """Reported, not hidden — the group admin screen still needs them."""
        assert TEAM_GROUP in {g.id for g in await groups.get_groups({})}
