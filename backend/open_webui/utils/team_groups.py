"""Which team, if any, a group belongs to.

One question, one answer, one place. Every guard that has to treat a team's own
group differently asks this function — none of them re-derives it from a name, a
prefix or a permission flag.

⚠️ The source of truth is the back-reference `teams.group_id`, and deliberately
nothing else:

  * **not the masking flag** — custom policy groups carry it too, so it says
    "this group enforces masking", never "this group belongs to a team"
  * **not the name prefix** — the name is derived FROM the team and SCIM can
    rewrite it (`routers/scim.py:911`), so it identifies nothing it is supposed to

`teams.group_id` is `UNIQUE`, which is what lets the answer be a single team
rather than a list.
"""

import logging
import time
from typing import Literal, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

#: A group nothing points at is not a team's group. Not "unknown" — the absence
#: of a back-reference is a complete answer.
TeamGroupKind = Optional[Literal['team_pii', 'team']]


async def team_group_kind(group_id: str, db: Optional[AsyncSession] = None) -> TeamGroupKind:
    """What kind of team group this is, or `None` if it is not one.

    Returns:
        ``'team_pii'`` — the team's PII policy group, the one `teams.group_id`
        points at. Its permissions and its name are derived from the team, so
        editing either of them directly is refused.

        ``'team'`` — reserved for a team's ordinary groups, which do not exist
        yet. The branch is written now, and returned by nothing, so that when
        they arrive the classification is extended HERE rather than growing a
        second `if` somewhere else. A three-valued answer that is currently
        two-valued costs nothing; a second source of truth costs a great deal.

        ``None`` — every other group: custom policy groups, the global policy
        group, and groups with no policy at all.

    ⚠️ Function-local imports below: `models.billing` pulls in `stripe` and the
    billing tables, and importing that at module scope would make every guard in
    `models.groups` depend on the billing stack loading cleanly.
    """
    if not group_id:
        return None

    from open_webui.internal.db import get_async_db_context
    from open_webui.models.billing import Team
    from sqlalchemy import select

    async with get_async_db_context(db) as session:
        result = await session.execute(select(Team.id).filter(Team.group_id == group_id))
        team_id = result.scalars().first()

    if team_id is None:
        return None

    return 'team_pii'


#: Exactly the key the group exists for, and nothing else. The sparseness is
#: load-bearing: OAuth writes a group's own permissions straight back to it
#: (`utils/oauth.py:1409`) and only substitutes instance defaults when the value
#: is FALSY. One truthy key keeps that branch dead. A key whose value were
#: *computed* would break it — the round-trip would write yesterday's answer.
TEAM_PII_GROUP_PERMISSIONS = {'chat': {'pii_masking_enforced': True}}

#: Enough of the uuid to tell two `PII — Marketing` apart in a dropdown. Identity
#: is the foreign key, never the name, so this only has to be readable — and the
#: collision it prevents is guaranteed, not hypothetical: the Stripe portal path
#: creates teams called "My Team" (`routers/billing.py:1915`).
TEAM_ID_DISCRIMINATOR_LENGTH = 8


def team_pii_group_name(team_name: str, team_id: str) -> str:
    """The derived name. Derived, therefore frozen — see the guard in `models/groups.py`.

    ⚠️ Never parse this back. The team a group belongs to is `teams.group_id` and
    nothing else; reading it out of the name would be the second source of truth
    that `team_group_kind` exists to prevent.
    """
    return f'PII — {team_name} · {team_id[:TEAM_ID_DISCRIMINATOR_LENGTH]}'


async def ensure_team_pii_group(team_id: str, db: Optional[AsyncSession] = None) -> Optional[str]:
    """The team's PII policy group, creating it if it is not there yet.

    Returns its id, or `None` if there is no such team.

    ⚠️ **This is a write reached from read paths, and that is a concession, not a
    design.** The group would be created in `create_team` if `create_team` could
    guarantee it happened together with the team — it cannot: the team is
    committed in its own transaction before anything else runs, session sharing
    is off by default (`env.py:356`), and the model methods commit internally.
    That is N-6, it has its own ticket, and when it is fixed this call moves into
    `create_team` and nothing else changes.

    ⚠️ **Reads before it writes, every time.** The dashboard calls three routes per
    screen and all three resolve the same scope, so a create-then-check shape
    would attempt three writes per page load — for every viewer, including an
    admin looking at somebody else's team. Once the group exists this function
    issues one SELECT and no write at all, which is what makes the concession
    affordable. There is deliberately no cache: a remembered "it exists" outlives
    the group it remembers, and the saving is two SELECTs.

    Idempotent, including against itself: two concurrent callers may race, and the
    `UNIQUE` index on `teams.group_id` decides. The loser re-reads and returns the
    winner's group rather than raising — a race is a normal outcome here, not an
    error.
    """
    from open_webui.internal.db import get_async_db_context
    from open_webui.models.billing import Team, Teams
    from open_webui.models.groups import Group, GroupForm, Groups
    from sqlalchemy import select

    async with get_async_db_context(db) as session:
        result = await session.execute(select(Team).filter(Team.id == team_id))
        team = result.scalars().first()

        if team is None:
            return None

        if team.group_id:
            existing = await session.execute(select(Group.id).filter(Group.id == team.group_id))
            if existing.scalars().first() is not None:
                return team.group_id
            # The reference outlived the group: `PRAGMA foreign_keys` is 0 on
            # SQLite, so deleting a group leaves `teams.group_id` dangling. Fall
            # through and make a new one rather than handing back an id that
            # resolves to nothing.
            log.warning(
                'team_groups: team %s points at missing group %s; creating a replacement',
                team_id,
                team.group_id,
            )

        team_name = team.name

    group = await Groups.insert_new_group(
        # Nobody made this; the product did. `user_id` is only read for display,
        # so an empty string is honest where naming an admin who did not do it
        # would not be. Same choice as migration 1782400007.
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

    try:
        await Teams.update(team_id, group_id=group.id, db=db)
    except IntegrityError:
        # Another caller won the race. Its group is the answer; ours is an orphan
        # nothing points at, which `team_group_kind` correctly classifies as not a
        # team group.
        log.info('team_groups: lost the race to create the group for team %s', team_id)

    async with get_async_db_context(db) as session:
        result = await session.execute(select(Team.group_id).filter(Team.id == team_id))
        return result.scalars().first()


async def rename_team_pii_group(
    team_id: str, team_name: str, db: Optional[AsyncSession] = None
) -> Optional[str]:
    """Keep the group's derived name in step with the team's.

    Called from the one route that changes a team's name
    (`routers/billing.py:1235`). The guard in `models/groups.py` refuses every
    other way to that name, so this is not one of several writers — it is the
    writer, and the guard is what makes that true.

    ⚠️ Writes through the ORM rather than `Groups.update_group_by_id`, which is
    the method that refuses exactly this change. Going around a guard is normally
    the wrong instinct; here the guard's whole purpose is to say "the name follows
    the team", and this is the code that makes the team's name arrive.

    Returns the group id it renamed, or `None` if the team has no group yet — not
    an error: under path B a team without a group is a normal state, and the name
    will be correct when the group is first created.
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
