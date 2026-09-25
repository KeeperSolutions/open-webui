"""A team's PII group cannot be renamed, have its permissions changed, or be deleted.

Its name and permissions are derived from the team. The guard lives in the model
because SCIM, LDAP and OAuth call `Groups` directly, not through the admin route.
The admin route and SCIM are tested separately so that a guard placed only in
the route would fail the SCIM test.

The guard refuses a change, never a restatement: OAuth writes a group's own
permissions back to it and SCIM resends the current name on every membership
edit, so refusing those would break directory sync.
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
    `team_group_kind` uses `internal.db`'s. With `DATABASE_ENABLE_SESSION_SHARING`
    off a passed session is ignored, so an unpatched one reads the developer's
    database.
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
        """Only derived fields are frozen; the description stays editable."""
        assert await groups.update_group_by_id(TEAM_GROUP, _form(description="New blurb")) is not None
        assert (await _stored(db_session, TEAM_GROUP)).description == "New blurb"

    @pytest.mark.asyncio
    async def test_restating_the_same_permissions_goes_through(self, groups, db_session):
        """Writing back the current permissions is allowed, because OAuth does it on every sync."""
        assert await groups.update_group_by_id(TEAM_GROUP, _form(permissions=ENFORCING)) is not None
        assert (await _stored(db_session, TEAM_GROUP)).permissions == ENFORCING

    @pytest.mark.asyncio
    async def test_restating_the_same_name_goes_through(self, groups, db_session):
        """SCIM resends the current name on every membership edit."""
        assert await groups.update_group_by_id(TEAM_GROUP, _form(name=TEAM_GROUP_NAME)) is not None

    @pytest.mark.asyncio
    async def test_a_custom_policy_group_is_untouched_by_this_guard(self, groups, db_session):
        """A group that enforces masking and is named like a team group, but has no team, stays editable."""
        assert await groups.update_group_by_id(
            CUSTOM_GROUP,
            GroupUpdateForm(name="Renamed", description="", permissions={"chat": {}}),
        ) is not None
        assert (await _stored(db_session, CUSTOM_GROUP)).name == "Renamed"


# ---------------------------------------------------------------------------
# deletion is refused through the model, the admin route and SCIM
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
                    request=MagicMock(),
                    id=TEAM_GROUP,
                    user=MagicMock(id="admin", role="admin"),
                    db=db_session,
                )
        assert exc.value.status_code == 400
        assert await _stored(db_session, TEAM_GROUP) is not None

    @pytest.mark.asyncio
    async def test_scim_route_refuses(self, groups, db_session):
        """SCIM has its own delete handler, so this fails if the guard moves into the admin route."""
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
    """The guard relies on `team_group_kind`, the single reader of `teams.group_id`.

    Patching that classifier changes the guard's answer; the structural test in
    `test_team_groups.py` checks no other module queries the column.
    """
    async def _says_not_a_team_group(group_id, db=None):
        return None

    with patch("open_webui.utils.team_groups.team_group_kind", _says_not_a_team_group):
        assert await groups.delete_group_by_id(TEAM_GROUP) is True


class TestIsTeamGroupFlag:
    """`GET /groups/` reports which groups a team owns, so the UI can exclude them.

    A flag rather than the team id, and reported rather than filtered: the admin
    group screen still lists team groups.
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
        """Team groups are reported, not hidden, because the group admin screen lists them."""
        assert TEAM_GROUP in {g.id for g in await groups.get_groups({})}
