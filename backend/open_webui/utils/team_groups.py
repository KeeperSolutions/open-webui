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
from typing import Literal, Optional

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
