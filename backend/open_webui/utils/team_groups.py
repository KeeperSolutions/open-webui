"""Which team, if any, a group belongs to.

Every guard that treats a team's own group differently asks this module instead
of deriving the answer from a name, a prefix or a permission flag.

The only source of truth is the back-reference `teams.group_id`:

  * not the masking flag: custom policy groups carry it too, so it only says the
    group enforces masking
  * not the name prefix: the name is derived from the team and SCIM can rewrite
    it

`teams.group_id` is unique, so the answer is at most one team.
"""

import logging
import time
from typing import Literal, NamedTuple, Optional

from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

#: `None` means no team points at the group. That is a definite answer, not
#: "unknown".
TeamGroupKind = Optional[Literal['team_pii', 'team']]


class TeamOwnership(NamedTuple):
    """The team that owns a group and that team's owner, read from one row.

    The authorisation guard needs both. Reading them in two queries would let the
    two answers describe different states of the same team.
    """

    team_id: str
    owner_user_id: str


async def team_ownership_of_group(
    group_id: str, db: Optional[AsyncSession] = None
) -> Optional[TeamOwnership]:
    """The team that owns this group and who owns that team, or `None`.

    This is the single reader of `teams.group_id`; the rest of this module and
    `utils/team_scope.py` build on it.
    `test_team_groups.py::test_group_id_is_read_in_exactly_one_module` fails on
    any `Team.group_id` access outside this module.

    The imports are function-local because `models.billing` pulls in `stripe`.
    Importing it at module scope would make every guard in `models.groups` depend
    on the billing stack loading.
    """
    if not group_id:
        # An empty id cannot match anything, so skip the query.
        return None

    from open_webui.internal.db import get_async_db_context
    from open_webui.models.billing import Team
    from sqlalchemy import select

    async with get_async_db_context(db) as session:
        result = await session.execute(
            select(Team.id, Team.owner_user_id).filter(Team.group_id == group_id)
        )
        row = result.first()

    return None if row is None else TeamOwnership(team_id=row[0], owner_user_id=row[1])


async def team_owning_group_id(group_id: str, db: Optional[AsyncSession] = None) -> Optional[str]:
    """The id of the team that owns this group, or `None`.

    Wraps `team_ownership_of_group` so there is still only one query.
    """
    ownership = await team_ownership_of_group(group_id, db=db)
    return None if ownership is None else ownership.team_id


async def team_group_kind(group_id: str, db: Optional[AsyncSession] = None) -> TeamGroupKind:
    """What kind of team group this is, or `None` if it is not one.

    Returns:
        ``'team_pii'`` — the team's PII policy group, the one `teams.group_id`
        points at. Its permissions and its name are derived from the team, so
        editing either of them directly is refused.

        ``'team'`` — reserved for a team's ordinary groups, which do not exist
        yet. Nothing returns it today; when they arrive, extend the
        classification here rather than adding a second check elsewhere.

        ``None`` — every other group: custom policy groups, the global policy
        group, and groups with no policy at all.

    Delegates to `team_owning_group_id` instead of querying itself.
    `test_team_group_kind_asks_the_owner_lookup` checks this, because the
    structural test cannot see a second query inside this module.
    """
    return None if await team_owning_group_id(group_id, db=db) is None else 'team_pii'


#: Only the key the group exists for. OAuth sync (`update_user_groups` in
#: `utils/oauth.py`) writes a group's permissions back to it and substitutes
#: instance defaults only when they are falsy, so one truthy key keeps the
#: defaults out. A computed value would be written back stale.
TEAM_PII_GROUP_PERMISSIONS = {'chat': {'pii_masking_enforced': True}}

#: Characters of the team id appended to the group name so two teams with the
#: same name get distinguishable groups in a dropdown. The Stripe webhook
#: (`_handle_stripe_event` in `routers/billing.py`) names teams "My Team", so
#: duplicates are common. Identity is the foreign key, never the name.
TEAM_ID_DISCRIMINATOR_LENGTH = 8


def team_pii_group_name(team_name: str, team_id: str) -> str:
    """The group name derived from the team; `models/groups.py` refuses direct edits.

    Never parse the team back out of this name. Use `teams.group_id`.
    """
    return f'PII — {team_name} · {team_id[:TEAM_ID_DISCRIMINATOR_LENGTH]}'


#: Fields a team's PII group derives from the team. Neither can be edited on the
#: group directly; change the team instead.
TEAM_GROUP_DERIVED_FIELDS = ('name', 'permissions')


def team_group_derived_changes(existing, changes: dict) -> list:
    """Which derived fields this form would change. Resending the current value is not a change.

    Shared by the model guard and the route so both agree. OAuth sync writes a
    group's own permissions back to it and SCIM resends the current name on every
    membership edit; treating those as changes would break directory sync.

    Pure and synchronous. It does not check whether the group belongs to a team;
    callers ask `team_group_kind` separately, so the route can run this cheap
    check first.

    Args:
        existing: the stored group — an ORM `Group` or a `GroupModel`; only
            attribute access is used, so either works.
        changes: the proposed values, already stripped of `None` fields the way
            `update_group_by_id` strips them.
    """
    return [
        field
        for field in TEAM_GROUP_DERIVED_FIELDS
        if field in changes and changes[field] != getattr(existing, field, None)
    ]


def team_group_flag_column(group_id_column):
    """A scalar subquery counting the teams that point at this group.

    For listings, where calling the async, per-group `team_group_kind` would cost
    one query per row. It lives here because `teams.group_id` may only be read in
    this module. The caller passes the group id column to correlate against.
    """
    from open_webui.models.billing import Team
    from sqlalchemy import func, select

    return (
        select(func.count(Team.id))
        .where(Team.group_id == group_id_column)
        .scalar_subquery()
    )


async def ensure_team_pii_group(team_id: str, db: Optional[AsyncSession] = None) -> Optional[str]:
    """The team's PII policy group, creating it if it does not exist yet.

    Returns its id, or `None` if there is no such team.

    This write is reached from read paths. `create_team` cannot create the group
    atomically with the team: the team commits in its own transaction, session
    sharing is off by default (`DATABASE_ENABLE_SESSION_SHARING`) and the model
    methods commit internally.

    It reads before it writes. The dashboard resolves the same scope on three
    routes per page load, so once the group exists this costs one SELECT and no
    write. There is no cache, because a cached "it exists" can outlive the group.

    Safe under concurrent calls. A conditional update picks the winner; the loser
    deletes the group it made and returns the winner's id instead of raising.
    """
    from open_webui.internal.db import get_async_db_context
    from open_webui.models.billing import Team
    from open_webui.models.groups import Group, GroupForm, Groups
    from sqlalchemy import delete, select, update

    async with get_async_db_context(db) as session:
        result = await session.execute(select(Team).filter(Team.id == team_id))
        team = result.scalars().first()

        if team is None:
            return None

        # The link below is only written if `group_id` still has this value
        # (`None` or a dangling id).
        observed_group_id = team.group_id

        if team.group_id:
            existing = await session.execute(select(Group.id).filter(Group.id == team.group_id))
            if existing.scalars().first() is not None:
                return team.group_id
            # SQLite runs with `PRAGMA foreign_keys` off, so deleting a group can
            # leave `teams.group_id` dangling. Create a replacement.
            log.warning(
                'team_groups: team %s points at missing group %s; creating a replacement',
                team_id,
                team.group_id,
            )

        team_name = team.name

    group = await Groups.insert_new_group(
        # No user created this group. `user_id` is only used for display, so it
        # is left empty, as in migration 1782400007.
        '',
        GroupForm(
            name=team_pii_group_name(team_name, team_id),
            description='',
            permissions=TEAM_PII_GROUP_PERMISSIONS,
        ),
        db=db,
    )
    if group is None:
        return None

    # The update is conditional on the value read above; that is what resolves
    # concurrent calls. `uq_teams_group_id` does not: racing callers write
    # different group ids to the same team row, which violates nothing, so an
    # unconditional update would let the last write win and orphan the other
    # group. An orphan carries the masking permission, is not recognised as a
    # team group and would show up in the admin's Enforce list. The race happens
    # in normal use: the dashboard's first load calls this from three routes.
    async with get_async_db_context(db) as session:
        result = await session.execute(
            update(Team)
            .where(
                Team.id == team_id,
                Team.group_id.is_(None)
                if observed_group_id is None
                else Team.group_id == observed_group_id,
            )
            # A raw update does not stamp `updated_at`, so set it explicitly.
            .values(group_id=group.id, updated_at=int(time.time()))
        )
        won = result.rowcount == 1
        await session.commit()

    if not won:
        # Another caller linked its group first. Delete ours: it is empty and
        # nothing points at it.
        log.info('team_groups: lost the race to create the group for team %s', team_id)
        async with get_async_db_context(db) as session:
            await session.execute(delete(Group).where(Group.id == group.id))
            await session.commit()

    async with get_async_db_context(db) as session:
        result = await session.execute(select(Team.group_id).filter(Team.id == team_id))
        return result.scalars().first()


async def rename_team_pii_group(
    team_id: str, team_name: str, db: Optional[AsyncSession] = None
) -> Optional[str]:
    """Keep the group's derived name in step with the team's name.

    Called from `update_team_name` in `routers/billing.py`, the only writer of this
    name. It bypasses `Groups.update_group_by_id` on purpose, because that method
    refuses direct edits to derived fields.

    Returns the renamed group's id, or `None` if the team has no group yet. That
    is a normal state; the group gets the right name when it is created.
    """
    from open_webui.internal.db import get_async_db_context
    from open_webui.models.billing import Team
    from open_webui.models.groups import Group
    from sqlalchemy import select, update

    async with get_async_db_context(db) as session:
        result = await session.execute(select(Team.group_id).filter(Team.id == team_id))
        group_id = result.scalars().first()
        if not group_id:
            return None

        await session.execute(
            update(Group)
            .filter_by(id=group_id)
            .values(name=team_pii_group_name(team_name, team_id), updated_at=int(time.time()))
        )
        await session.commit()
        return group_id


async def remove_from_team_policy_group(
    team_id: str, user_id: str, db: Optional[AsyncSession] = None
) -> bool:
    """Remove a user from their team's PII policy group. Returns whether it acted.

    Called from `remove_team_member` in `routers/billing.py` when a person leaves
    the team.

    Returns `False` and writes no audit row when the team has no policy group or
    the user is not in it. The audit table records transitions, so a
    `member_removed` row for a no-op would falsely read as protection removed.

    The audit row is written before the membership is removed, and a failed audit
    write stops the removal. A record without a change can be seen and
    reconciled; a change without a record cannot. `_audit_membership_change` in
    `routers/groups.py` uses the same order. Do not reverse it.

    Raises `RuntimeError` if the removal fails after the audit row is written.
    `remove_users_from_group` returns `None` on database errors, and returning
    `True` or `False` there would hide that the audit row and the membership now
    disagree.
    """
    from open_webui.internal.db import get_async_db_context
    from open_webui.models.billing import Team
    from open_webui.models.groups import Groups
    from open_webui.models.pii_policy_audit import (
        EVENT_MEMBER_REMOVED,
        PiiPolicyAudits,
        REASON_LEFT_TEAM,
        SYSTEM_ACTOR_EMAIL,
        SYSTEM_ACTOR_ID,
    )
    from sqlalchemy import select

    async with get_async_db_context(db) as session:
        result = await session.execute(select(Team.group_id).filter(Team.id == team_id))
        group_id = result.scalars().first()

    if not group_id:
        return False

    if user_id not in set(await Groups.get_group_user_ids_by_id(group_id, db=db)):
        return False

    await PiiPolicyAudits.insert_event(
        event_type=EVENT_MEMBER_REMOVED,
        group_id=group_id,
        user_id=user_id,
        actor_user_id=SYSTEM_ACTOR_ID,
        actor_email=SYSTEM_ACTOR_EMAIL,
        reason=REASON_LEFT_TEAM,
        db=db,
    )

    # The model refuses removals from an enforcing group without a reason, as a
    # backstop for callers that bypass audited routes, so pass it here too.
    removed = await Groups.remove_users_from_group(
        group_id, [user_id], reason=REASON_LEFT_TEAM, db=db
    )
    if removed is None:
        # A reason was passed, so `None` here is a database failure, not a
        # refusal. Raise: `False` means "nothing to do". The user keeps their
        # masking, which is the safe direction.
        log.error(
            'team_groups: audit row says user %s left the policy group %s of team %s, '
            'but the removal failed; the two now disagree',
            user_id,
            group_id,
            team_id,
        )
        raise RuntimeError(
            f'Failed to remove user {user_id} from the policy group of team {team_id} '
            'after recording the removal'
        )

    return True
