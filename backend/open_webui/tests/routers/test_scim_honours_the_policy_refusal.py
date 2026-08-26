"""Directory sync must not answer a REFUSED membership change with success.

The guard that keeps SCIM from silently unmasking people lives in the model, and
it works: `set_group_user_ids_by_id` returns `False` and `remove_users_from_group`
returns `None` when the change would drop somebody out of a group that enforces
PII masking without a reason.

⚠️ Both return values used to be discarded by these routes. The request carried
on and answered `200` with the group, so the identity provider recorded a
successful sync for a change the database had refused — and kept believing it was
in step, with nothing anywhere to say otherwise. A refusal nobody is told about
is indistinguishable from no refusal at all.

⚠️ The refusal is NARROW on purpose, and one test here exists only to prove that:
SCIM may still add people to an enforcing group. Refusing the whole call would
stop directory sync managing the group at all, including the direction that never
takes protection away.
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

from open_webui.models.billing import Team, TeamMember
from open_webui.models.groups import Group, GroupMember
from open_webui.models.users import User

GROUP = "g-policy"
PLAIN = "g-plain"
TEAM, TEAM_GROUP = "t1", "g-team"
KEEP, DROP = "u-keep", "u-drop"
ENFORCING = {"chat": {"pii_masking_enforced": True}}


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        # `teams` too: updating a group asks `team_group_kind` whether it
        # belongs to one, and that reads `teams.group_id`.
        for table in (Group, GroupMember, User, Team, TeamMember):
            await conn.run_sync(table.__table__.create, checkfirst=True)

    db = async_sessionmaker(bind=engine, expire_on_commit=False)()
    now = int(time.time())
    db.add_all(
        [
            Group(
                id=GROUP,
                user_id="",
                name="PII Masking Policy",
                description="",
                data={},
                meta=None,
                permissions=ENFORCING,
                created_at=now,
                updated_at=now,
            ),
            Group(
                id=PLAIN,
                user_id="",
                name="Marketing",
                description="",
                data={},
                meta=None,
                permissions={"chat": {"pii_masking_enforced": False}},
                created_at=now,
                updated_at=now,
            ),
            GroupMember(id="m1", group_id=GROUP, user_id=KEEP, created_at=now, updated_at=now),
            GroupMember(id="m2", group_id=GROUP, user_id=DROP, created_at=now, updated_at=now),
            # A team, its own policy group, and one member — so the OTHER refusal
            # has something to fire on.
            Group(
                id=TEAM_GROUP,
                user_id="",
                name="PII — Acme · t1",
                description="",
                data={},
                meta=None,
                permissions=ENFORCING,
                created_at=now,
                updated_at=now,
            ),
            Team(
                id=TEAM,
                name="Acme",
                owner_user_id=KEEP,
                seat_limit=10,
                monthly_credits=0,
                group_id=TEAM_GROUP,
                created_at=now,
                updated_at=now,
            ),
            TeamMember(id="tm1", team_id=TEAM, user_id=KEEP, role="owner", created_at=now),
            GroupMember(id="m3", group_id=TEAM_GROUP, user_id=KEEP, created_at=now, updated_at=now),
        ]
    )
    for uid in (KEEP, DROP):
        db.add(
            User(
                id=uid,
                name=uid,
                email=f"{uid}@x.com",
                role="user",
                profile_image_url="",
                last_active_at=now,
                created_at=now,
                updated_at=now,
            )
        )
    await db.commit()

    @asynccontextmanager
    async def _ctx(_db=None):
        yield db

    with patch("open_webui.internal.db.get_async_db_context", _ctx), patch(
        "open_webui.models.groups.get_async_db_context", _ctx
    ), patch("open_webui.models.users.get_async_db_context", _ctx), patch(
        "open_webui.models.billing.get_async_db_context", _ctx
    ):
        yield db

    await db.close()
    await engine.dispose()


async def _members(db, group_id=GROUP):
    result = await db.execute(select(GroupMember.user_id).filter(GroupMember.group_id == group_id))
    return {uid for (uid,) in result.all()}


def _refused(response):
    """A SCIM error body, not a group. Both halves matter."""
    import json

    if getattr(response, "status_code", None) != 400:
        return False
    body = json.loads(response.body)
    return body.get("scimType") == "mutability"


async def _put(db, member_ids):
    from open_webui.routers import scim
    from open_webui.routers.scim import SCIMGroupMember, SCIMGroupUpdateRequest

    return await scim.update_group(
        group_id=GROUP,
        request=MagicMock(),
        group_data=SCIMGroupUpdateRequest(
            displayName="PII Masking Policy",
            members=[SCIMGroupMember(value=uid) for uid in member_ids],
        ),
        db=db,
    )


async def _patch(db, operations, group_id=GROUP):
    from open_webui.routers import scim
    from open_webui.routers.scim import SCIMPatchOperation, SCIMPatchRequest

    return await scim.patch_group(
        group_id=group_id,
        request=MagicMock(),
        patch_data=SCIMPatchRequest(Operations=[SCIMPatchOperation(**op) for op in operations]),
        db=db,
    )


# ---------------------------------------------------------------------------
# PUT /Groups/{id} — "set the membership to this list"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_put_that_drops_a_member_is_answered_with_an_error(session):
    response = await _put(session, [KEEP])
    assert _refused(response)


@pytest.mark.asyncio
async def test_and_the_membership_is_unchanged(session):
    """⚠️ The other half. A route that reported the refusal but let the write
    through would pass the test above."""
    await _put(session, [KEEP])
    assert await _members(session) == {KEEP, DROP}


@pytest.mark.asyncio
async def test_a_put_that_only_adds_people_still_works(session):
    """⚠️ The refusal is narrow, and this is what proves it.

    Adding never takes protection away, so directory sync keeps managing the
    group in that direction. A blanket refusal would look identical to a correct
    guard on every other test in this file.
    """
    response = await _put(session, [KEEP, DROP, "u-new"])
    assert not _refused(response)
    assert await _members(session) == {KEEP, DROP, "u-new"}


# ---------------------------------------------------------------------------
# PATCH /Groups/{id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_patch_replacing_members_is_answered_with_an_error(session):
    response = await _patch(session, [{"op": "replace", "path": "members", "value": [{"value": KEEP}]}])
    assert _refused(response)
    assert await _members(session) == {KEEP, DROP}


@pytest.mark.asyncio
async def test_a_patch_removing_one_member_is_answered_with_an_error(session):
    response = await _patch(
        session, [{"op": "remove", "path": f'members[value eq "{DROP}"]'}]
    )
    assert _refused(response)
    assert await _members(session) == {KEEP, DROP}


@pytest.mark.asyncio
async def test_a_failure_on_a_group_with_no_policy_is_not_called_a_policy_refusal(session):
    """⚠️ `remove_users_from_group` returns `None` for a missing group and for an
    unhandled exception too, not only for the refusal.

    Reporting every `None` as a policy refusal would tell an administrator that
    masking blocked a change masking had nothing to do with — so the group's own
    permissions decide which answer comes back.
    """
    import json

    from open_webui.models.groups import Groups

    with patch.object(Groups, "remove_users_from_group", return_value=None):
        response = await _patch(
            session, [{"op": "remove", "path": f'members[value eq "{DROP}"]'}], group_id=PLAIN
        )

    assert response.status_code == 500
    assert json.loads(response.body).get("scimType") != "mutability"


# ---------------------------------------------------------------------------
# The second rule, and the message telling them apart
# ---------------------------------------------------------------------------


def _refused_because(response, needle):
    import json

    return response.status_code == 400 and needle in json.loads(response.body)["detail"]


@pytest.mark.asyncio
async def test_directory_sync_cannot_put_an_outsider_in_a_team_group(session):
    """A team's group is derived from the team, membership included."""
    from open_webui.routers import scim
    from open_webui.routers.scim import SCIMGroupMember, SCIMGroupUpdateRequest

    response = await scim.update_group(
        group_id=TEAM_GROUP,
        request=MagicMock(),
        group_data=SCIMGroupUpdateRequest(
            displayName="PII — Acme · t1",
            members=[SCIMGroupMember(value=KEEP), SCIMGroupMember(value=DROP)],
        ),
        db=session,
    )
    assert _refused_because(response, "belongs to a team")
    assert await _members(session, TEAM_GROUP) == {KEEP}


@pytest.mark.asyncio
async def test_the_two_refusals_do_not_borrow_each_other_s_message(session):
    """⚠️ Both rules answer with the same falsy value, so the route has to ask
    which one fired. Reporting the wrong one tells an administrator that masking
    blocked a change masking had nothing to do with."""
    from open_webui.routers import scim
    from open_webui.routers.scim import SCIMGroupMember, SCIMGroupUpdateRequest

    dropping = await scim.update_group(
        group_id=GROUP,
        request=MagicMock(),
        group_data=SCIMGroupUpdateRequest(
            displayName="PII Masking Policy", members=[SCIMGroupMember(value=KEEP)]
        ),
        db=session,
    )
    assert _refused_because(dropping, "enforces PII masking")
    assert not _refused_because(dropping, "belongs to a team")


@pytest.mark.asyncio
async def test_a_patch_that_adds_an_outsider_to_a_team_group_is_refused(session):
    from open_webui.routers import scim
    from open_webui.routers.scim import SCIMPatchOperation, SCIMPatchRequest

    response = await scim.patch_group(
        group_id=TEAM_GROUP,
        request=MagicMock(),
        patch_data=SCIMPatchRequest(
            Operations=[SCIMPatchOperation(op="add", path="members", value=[{"value": DROP}])]
        ),
        db=session,
    )
    assert _refused_because(response, "belongs to a team")
    assert await _members(session, TEAM_GROUP) == {KEEP}
