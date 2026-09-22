"""Team scoping for the PII dashboard (read-only).

Shared by `routers/langfuse.py` and `routers/users.py`. Kept out of
`utils/access_control/`, which is upstream code, following the example of
`utils/pii_policy.py`.

Nothing here resolves a user to a team. Every entry point starts from a
`team_id` in the request. `TeamMembers.get_by_user_id` and
`Teams.get_by_owner_user_id` return `.first()`, which cannot tell a duplicate
from a unique hit, so they must not be used for authorisation.
"""

import logging
from typing import NamedTuple, Optional

from fastapi import HTTPException, status
from open_webui.constants import ERROR_MESSAGES
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)


#: The code points JavaScript `String.prototype.trim` removes: the WhiteSpace and
#: LineTerminator productions, including U+FEFF.
#:
#: `str.strip()` differs: it keeps U+FEFF and strips U+001C-U+001F and U+0085.
#: A mismatch does not fail, it silently drops metrics rows. Listed as code
#: points because a literal string would be a run of invisible characters.
_JS_TRIM_CHARS = ''.join(
    chr(c)
    for c in (
        0x0009, 0x000A, 0x000B, 0x000C, 0x000D,  # tab, LF, VT, FF, CR
        0x0020, 0x00A0, 0x1680,                  # space, NBSP, OGHAM SPACE MARK
        *range(0x2000, 0x200B),                  # EN QUAD .. HAIR SPACE, ZWSP
        0x2028, 0x2029,                          # LINE / PARAGRAPH SEPARATOR
        0x202F, 0x205F, 0x3000,                  # NNBSP, MMSP, IDEOGRAPHIC SPACE
        0xFEFF,                                  # BOM / ZWNBSP
    )
)


def normalize_user_key(user: Optional[str]) -> str:
    """Identity key for a Langfuse `user` value.

    Must match `normalizeUserKey` in
    `src/lib/components/admin/PiiDashboard/sections/costAnalytics.ts`, which is
    `(user ?? '').trim().toLowerCase()`. A divergence raises nothing: the backend
    silently drops rows the frontend would attribute. Both sides are pinned by
    literal expectations, in `tests/test_team_scope.py` and the matching vitest
    files, rather than by each other.

    Only leading and trailing whitespace is removed. Internal whitespace is kept,
    as `trim` keeps it.
    """
    return (user or '').strip(_JS_TRIM_CHARS).lower()


class TeamIdentities(NamedTuple):
    """A team's members as OWUI user ids and as normalised Langfuse keys.

    `ids` feed the directory filter; `keys` feed the metrics row filter. Both are
    sets, so a duplicate `team_members` row counts once.

    Pass `list(ids)`, not the set, to `Users.get_users`: its "both empty means no
    users" guard applies only to lists, and a set skips it and returns every user.
    """

    ids: frozenset
    keys: frozenset
    #: The team's PII policy group, or `None` when it has none yet. Set only by
    #: `resolve_dashboard_scope`; `resolve_team_identities` must not touch groups.
    group_id: Optional[str] = None


async def resolve_team_identities(
    team_id: str, db: Optional[AsyncSession] = None
) -> TeamIdentities:
    """Every identity belonging to one team.

    `keys` holds both the members' emails and their ids, because the frontend
    attributes a Langfuse row under either (`claimKeys` in `usersAccess.ts`). The
    empty key is excluded, as in `claimKeys`; otherwise a member with an empty
    email would claim every unattributed row.

    A team with no members and a team that does not exist both return empty sets.
    The caller must treat an empty scope as a refusal, never as an unfiltered read.
    """
    # Imported here so `normalize_user_key` can be imported and tested without
    # initialising the async database engine.
    from open_webui.models.billing import TeamMembers
    from open_webui.models.users import Users

    members = await TeamMembers.get_by_team_id(team_id, db=db)
    ids = {m.user_id for m in members}
    if not ids:
        return TeamIdentities(frozenset(), frozenset())

    users = await Users.get_users_by_user_ids(list(ids), db=db)

    # Built from the users found, not the membership rows: a member row pointing
    # at a deleted account is dropped, so the directory is never filtered on a
    # user it cannot return.
    found_ids = {u.id for u in users}
    missing = ids - found_ids
    if missing:
        log.warning(
            'team_scope: %d of %d members of team %s have no user record',
            len(missing),
            len(ids),
            team_id,
        )

    keys = set()
    for user in users:
        for traced in (user.email, user.id):
            key = normalize_user_key(traced)
            if key:
                keys.add(key)

    return TeamIdentities(frozenset(found_ids), frozenset(keys))


async def _may_read_team_dashboard(
    user, team_id: str, db: Optional[AsyncSession] = None
) -> bool:
    """Whether this user may read one team's dashboard: an admin or the team's owner.

    Modelled on `_may_read_pii_audit` in `routers/groups.py`, so a new role that
    gains access is one more `or` condition.

    Ownership comes from `teams.owner_user_id`, never `team_members.role`. Both are
    written at team creation and nothing keeps them in sync, so only one can be
    authoritative. The team is fetched by primary key: `owner_user_id` has no
    index, and `Teams.get_by_owner_user_id` may return an arbitrary row. The
    caller's `team_members` rows are not consulted, so a missing or duplicate row
    does not change the answer.
    """
    if user.role == 'admin':
        return True

    from open_webui.models.billing import Teams

    team = await Teams.get_by_id(team_id, db=db)
    return team is not None and team.owner_user_id == user.id


def _governs(user, ownership) -> bool:
    """Whether `user` owns the team in `ownership`.

    Shared by `may_manage_team_policy` and the membership-change guard so the
    response flag and the write guard cannot disagree.
    """
    return ownership is not None and ownership.owner_user_id == user.id


async def may_manage_team_policy(
    user, group_id: Optional[str], db: Optional[AsyncSession] = None
) -> bool:
    """Whether this viewer may manage the team's policy group (ownership check only).

    Used while rendering a page, when there are no targets yet. The write guard,
    `authorise_policy_membership_change`, also checks that every target is a team
    member and refuses a request with no targets, so it cannot be used here.

    Do not derive this from "the view is team-scoped". Today only admins and
    owners can read a scoped dashboard, so the two agree, but once
    `_may_read_team_dashboard` admits plain members that shortcut would give them
    the owner's rights.
    """
    if not group_id:
        # No policy group means nothing to manage, for every role.
        return False

    if user.role == 'admin':
        return True

    from open_webui.utils.team_groups import team_ownership_of_group

    return _governs(user, await team_ownership_of_group(group_id, db=db))


async def _may_change_policy_membership(
    user,
    group_id: str,
    target_user_ids: Optional[list],
    db: Optional[AsyncSession] = None,
) -> bool:
    """Whether this user may add or remove people in a team's PII policy group.

    Two independent checks; neither covers the other:

      1. The group is the policy group of a team the user owns. Without it an
         owner could write into an admin's group or another team's policy.
      2. Every target is a member of that team. Without it an owner could impose
         masking on any account on the instance.

    The tests cover each check with non-overlapping cases.

    Ownership comes from `teams.owner_user_id`, never `team_members.role`, as in
    `_may_read_team_dashboard`. The team is found from the group through a unique
    index, not through `Teams.get_by_owner_user_id`, which returns an arbitrary
    row for an owner of two teams.

    This is the authorisation boundary; the frontend `mayActFor` only decides
    what is shown.
    """
    if not target_user_ids:
        # Fail closed on an empty request, before checking the role. With no
        # targets, check 2 would pass vacuously. A caller that wants an empty body
        # to be a no-op must handle that before calling this.
        return False

    if user.role == 'admin':
        # Admins are not bound by team boundaries. No query needed.
        return True

    from open_webui.utils.team_groups import team_ownership_of_group

    ownership = await team_ownership_of_group(group_id, db=db)
    if not _governs(user, ownership):
        return False

    from open_webui.models.billing import TeamMembers

    members = await TeamMembers.members_among(ownership.team_id, list(target_user_ids), db=db)
    # Every target must be a member; a request naming a member and a stranger is
    # refused whole. Partial success would let an owner probe who exists outside
    # their team.
    return not (set(target_user_ids) - members)


async def authorise_policy_membership_change(
    user,
    group_id: str,
    target_user_ids: Optional[list],
    db: Optional[AsyncSession] = None,
) -> None:
    """Raise 401 unless the user may change these policy group memberships.

    Routers call this, never `_may_change_policy_membership`: a caller cannot
    fail open by forgetting or inverting a check on a value it never receives.
    An empty target list is refused; routes that treat an empty body as a no-op
    short-circuit before calling this (see `routers/groups.py`).
    `test_no_route_calls_the_guard_yet` in `tests/test_policy_membership_authz.py`
    keeps the boolean out of the routers.
    """
    if not await _may_change_policy_membership(user, group_id, target_user_ids, db=db):
        raise _prohibited()


def _prohibited() -> HTTPException:
    """The single refusal this module raises, so every route refuses the same way.

    401 rather than 404, matching `routers/groups.py`. A 404 would hide that the
    team exists, but team ids are `uuid4` values and cannot be guessed. Revisit
    this if team ids become sequential or slugs.
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
    )


async def resolve_dashboard_scope(
    user, team_id: Optional[str], db: Optional[AsyncSession] = None
) -> Optional[TeamIdentities]:
    """Authorise a dashboard request and return its team scope.

    Must be the first executable line of every scoped dashboard route. Returns
    `None` for the unscoped instance-wide view, which is admin-only, or the
    team's identities. It never returns an empty scope; an empty team is refused
    here, so callers have no empty-scope branch to get wrong.

    This is the one check whose failure exposes other teams' data rather than an
    empty screen, which is why it is a single shared function and raises instead
    of returning a value the caller must check.
    """
    if team_id is None:
        # The instance-wide view is admin-only.
        if user.role != 'admin':
            raise _prohibited()
        return None

    if not await _may_read_team_dashboard(user, team_id, db=db):
        raise _prohibited()

    scope = await resolve_team_identities(team_id, db=db)
    if not scope.ids:
        # No members, or none whose accounts still exist. Refused before the
        # caller queries anything, because filtering on an empty set returns
        # everything.
        log.warning('team_scope: refusing dashboard for team %s, scope is empty', team_id)
        raise _prohibited()

    # Only the scoped path reaches this, so an admin reading the unscoped
    # dashboard never writes anything. `ensure_team_pii_group` reads first, so a
    # team that already has its group costs one SELECT and no write.
    from open_webui.utils.team_groups import ensure_team_pii_group

    try:
        group_id = await ensure_team_pii_group(team_id, db=db)
    except Exception as e:
        # Best-effort: the group only labels masking as "team policy" on the
        # dashboard, and `teamGroupId: null` is a supported state. A failed write
        # must not take the dashboard down.
        log.warning('team_scope: could not resolve the policy group for team %s: %s', team_id, e)
        group_id = None

    return scope._replace(group_id=group_id)


def team_directory_filter(scope: TeamIdentities) -> dict:
    """The `Users.get_users` filter that limits the directory to one team.

    `group_ids: []` is required. `Users.get_users` treats an empty `user_ids` as
    no filter, and its guard against that applies only when `user_ids` and
    `group_ids` are both lists. Without `group_ids` an empty scope returns the
    whole instance. `resolve_dashboard_scope` refusing empty scopes is the first
    barrier; this is the second, because the guard lives in `models/users.py`
    and can change independently.

    `user_ids` is sorted so the filter is stable across processes.
    """
    return {'user_ids': sorted(scope.ids), 'group_ids': []}


#: A looser key: case-folded with all whitespace removed. Used only to detect
#: near misses in `scope_metric_rows`.
def _loose_user_key(value: Optional[str]) -> str:
    return ''.join((value or '').split()).casefold()


def scope_metric_rows(
    rows: list, scope: Optional[TeamIdentities], team_id: Optional[str] = None
) -> list:
    """Keep only the Langfuse rows belonging to one team.

    `scope=None` is the unscoped view and returns `rows` unchanged. A row is kept
    when its normalised key is a member's email or id. `"(unknown)"` belongs to
    nobody and is dropped.

    Rows that match a member under `_loose_user_key` but not under
    `normalize_user_key` are counted and logged. That count is zero unless the
    backend and frontend normalisations have drifted apart, which would otherwise
    drop rows silently. The log holds a count and a team id, never a key, because
    a Langfuse key is an email.
    """
    if scope is None:
        return rows

    loose = {_loose_user_key(k) for k in scope.keys}
    kept, near_misses = [], 0
    for row in rows:
        raw = row.get('user', '')
        if normalize_user_key(raw) in scope.keys:
            kept.append(row)
        elif _loose_user_key(raw) in loose:
            near_misses += 1

    if near_misses:
        log.warning(
            'team_scope: %d row(s) for team %s matched a member only under the loose key; '
            'the two normalisations have drifted apart',
            near_misses,
            team_id,
        )
    return kept
