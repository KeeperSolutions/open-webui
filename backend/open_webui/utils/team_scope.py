"""Team scoping for the PII dashboard — level A, read only.

Two routers (`langfuse.py` and `users.py`) need the same answers, so neither can
own them. Deliberately NOT in `utils/access_control/` — that module is upstream
(`tim@openwebui.com`), and this follows the precedent `utils/pii_policy.py` sets
for helpers of our own.

Names without a leading underscore are the module\'s surface, imported by both
routers. A leading underscore here means "only this module calls it", which is
why `_may_read_team_dashboard` will keep one and `resolve_team_identities` does
not.

⚠️ Nothing here resolves a USER to a team, and nothing here should. Level A never
asks that question: every entry point starts from a `team_id` that arrived in the
address. The `.first()` readers that would answer it — `TeamMembers.get_by_user_id`
and `Teams.get_by_owner_user_id` — are deliberately left untouched, because under
`.first()` a duplicate and a unique hit are indistinguishable.
"""

import logging
from typing import NamedTuple, Optional

from fastapi import HTTPException, status
from open_webui.constants import ERROR_MESSAGES
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)


#: Exactly the code points `String.prototype.trim` removes: the WhiteSpace and
#: LineTerminator productions, U+FEFF included.
#:
#: ⚠️ Spelled out rather than left to `str.strip()`, which disagrees with `trim`
#: in five places — measured, not assumed. Python does not strip U+FEFF, and it
#: DOES strip U+001C-U+001F and U+0085, none of which `trim` touches. Each of those
#: is a key the two sides would normalise differently, and a difference here does
#: not fail — it silently drops a row. Listed as code points so the set is
#: reviewable; a literal string here would be a run of invisible characters.
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

    Behaviour must stay identical to `normalizeUserKey`
    (`src/lib/components/admin/PiiDashboard/sections/costAnalytics.ts:14-16`),
    which is `(user ?? \'\').trim().toLowerCase()`.

    ⚠️ A divergence between the two does NOT raise and does NOT fail a request.
    The backend simply drops rows the frontend would have attributed, on a screen
    that claims to be exhaustive — and in the owner\'s scoped view the missing row
    disappears from both sides of the reconciliation, so the arithmetic stays
    green. That is why the two sides are pinned by literal expectations rather
    than by each other; the Python literals live in `tests/test_team_scope.py` and
    name the JS tests that pin the other side.

    Leading and trailing whitespace only. **Internal whitespace is preserved**,
    because `trim` preserves it, and a stricter rule here would fold two keys the
    frontend keeps apart.
    """
    return (user or '').strip(_JS_TRIM_CHARS).lower()


class TeamIdentities(NamedTuple):
    """Who a team is, in the two vocabularies the dashboard needs.

    `ids` are OWUI user ids, for the directory filter. `keys` are normalised
    Langfuse identity keys, for the metrics row filter.

    ⚠️ Both are **sets**. A duplicate row in `team_members` would otherwise put the
    same person in twice: the directory filter would carry the id twice and the
    returned `total` would disagree with the number of rows rendered.

    ⚠️ And because `ids` is a set, a caller building the directory filter must pass
    `list(ids)`. `models/users.py:448` arms its "both empty means no users" guard
    only for values that are `isinstance(..., list)` — a set skips that guard
    exactly like a missing key does, and an unfiltered read returns the whole
    instance. That is the second barrier\'s problem to solve, not this one\'s, but
    it is recorded here because this is where the type is chosen.
    """

    ids: frozenset
    keys: frozenset
    #: The team's own PII policy group, or `None` when the team has none yet.
    #:
    #: ⚠️ Defaulted, and populated only by `resolve_dashboard_scope`.
    #: `resolve_team_identities` answers "who is in this team" and must stay
    #: answerable without touching groups at all — the migration and the tests
    #: both call it that way.
    group_id: Optional[str] = None


async def resolve_team_identities(
    team_id: str, db: Optional[AsyncSession] = None
) -> TeamIdentities:
    """Every identity belonging to one team, starting from the team.

    `keys` covers **both** the members\' emails and their ids, because the frontend
    claims a Langfuse row under either
    (`src/lib/components/admin/PiiDashboard/sections/usersAccess.ts:169-179`). A
    narrower filter here would drop rows the frontend knows how to attribute.

    The empty key is excluded, mirroring `claimKeys`\' `if (key && ...)` guard on
    `usersAccess.ts:174`. Without it a member whose email is empty would claim
    every row Langfuse recorded against no one.

    Returns empty sets for a team with no members, and for a team that does not
    exist — the two are indistinguishable here on purpose, because both mean the
    same thing to the caller. Deciding what an empty scope means is the caller\'s
    job: a 401 before any further query, never an unfiltered read.
    """
    # Imported here rather than at module scope so `normalize_user_key` — a pure
    # string function with no database in it — can be imported and tested without
    # initialising the async engine. `models/users.py:565` and
    # `routers/billing.py:1134` import models function-locally for the same reason.
    from open_webui.models.billing import TeamMembers
    from open_webui.models.users import Users

    members = await TeamMembers.get_by_team_id(team_id, db=db)
    ids = {m.user_id for m in members}
    if not ids:
        return TeamIdentities(frozenset(), frozenset())

    users = await Users.get_users_by_user_ids(list(ids), db=db)

    # Built from the users actually found, not from the membership rows: a member
    # row pointing at a deleted account contributes no key, and its id is dropped
    # with it. Keeping such an id would filter the directory on a user the
    # directory cannot return, which reads on screen as a silently short list.
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
    """Who may read one team's dashboard.

    Modelled on the only precedent for widening an audience in place,
    `_may_read_pii_audit` (`routers/groups.py:302-312`): one function, so the next
    role that gains access is an `or` on a line rather than a second route.

    Underscored because its only caller is `resolve_dashboard_scope`, in this
    module. Everything the routers import is named without one.

    Ownership is read from `teams.owner_user_id` (`models/billing.py:192`) and
    NEVER from `team_members.role` (`:325`). Both are written when a team is
    created (`routers/billing.py:1056-1062`, `:1921-1927`) and nothing keeps them
    in step afterwards, so exactly one of them has to be the answer.

    ⚠️ The team is fetched by primary key, so this never searches by
    `owner_user_id` — a column with no index at all, whose `.first()` reader
    (`Teams.get_by_owner_user_id`) is therefore free to return an arbitrary row.
    Reading ownership OFF a row found by id has no such freedom.

    ⚠️ And it does not resolve the caller's own team. An owner whose
    `team_members` row is missing, and an owner who somehow has two, both still
    pass: ownership lives on `teams`, and authorisation must not fail on the state
    of a table it does not need to ask about.
    """
    if user.role == 'admin':
        return True

    from open_webui.models.billing import Teams

    team = await Teams.get_by_id(team_id, db=db)
    return team is not None and team.owner_user_id == user.id


def _prohibited() -> HTTPException:
    """The one refusal this module makes, so all three routes refuse alike.

    401 rather than 404, matching `routers/groups.py:326-329`. The case for 404 is
    that 401 confirms another team exists — but a `team_id` is a `uuid4`
    (`models/billing.py:240`), so it cannot be guessed or enumerated, and the
    disclosure 404 would prevent has nothing to disclose to. If team ids ever
    become slugs or sequential, this is the line that changes.
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
    )


async def resolve_dashboard_scope(
    user, team_id: Optional[str], db: Optional[AsyncSession] = None
) -> Optional[TeamIdentities]:
    """The first executable line of every scoped dashboard route.

    Returns the team's identities, or `None` meaning "no scoping, behave exactly
    as before". It never returns an empty scope: a caller therefore has no
    "if the scope is empty" branch to get wrong, because an empty scope is a
    refusal that happens here.

    ⚠️ This is the only place in level A that can fail **open**. Every other way
    this feature can break yields an empty screen; this one yields other people's
    data. That is why it is one function rather than three copies of a pattern in
    three routers, and why it raises rather than returning a value the caller has
    to remember to check.
    """
    if team_id is None:
        # The unscoped, instance-wide view. Today's behaviour, today's audience:
        # the `get_admin_user` dependency these routes used to carry is not gone,
        # it moved here.
        if user.role != 'admin':
            raise _prohibited()
        return None

    if not await _may_read_team_dashboard(user, team_id, db=db):
        raise _prohibited()

    scope = await resolve_team_identities(team_id, db=db)
    if not scope.ids:
        # A team with no members, or none whose accounts still exist. Refused
        # BEFORE the caller queries anything, because the alternative — filtering
        # on an empty set — is precisely the fail-open below.
        log.warning('team_scope: refusing dashboard for team %s, scope is empty', team_id)
        raise _prohibited()

    # ⚠️ Reached ONLY on the scoped path. The instance-wide branch returned above
    # without coming near this, which is the whole level-A guarantee: an admin
    # reading the unscoped dashboard creates nothing and writes nothing.
    #
    # ⚠️ And this is where a read becomes a write — see `ensure_team_pii_group`.
    # It reads first, so a team that already has its group costs one SELECT and
    # no write, which is what makes three routes per screen affordable.
    from open_webui.utils.team_groups import ensure_team_pii_group

    try:
        group_id = await ensure_team_pii_group(team_id, db=db)
    except Exception as e:
        # ⚠️ Best-effort, and the distinction matters: the group is what section 4
        # uses to say "masked by team policy" instead of "masked somewhere else".
        # It is a LABEL. Letting a failed write take the whole dashboard down would
        # trade a read that works for a write that is only a convenience — and
        # `teamGroupId: null` is already a supported state, so there is a correct
        # thing to fall back to.
        log.warning('team_scope: could not resolve the policy group for team %s: %s', team_id, e)
        group_id = None

    return scope._replace(group_id=group_id)


def team_directory_filter(scope: TeamIdentities) -> dict:
    """The `Users.get_users` filter keys that scope the directory to one team.

    ⚠️ `group_ids: []` is not padding, and removing it does not fail a test in the
    router that uses it — it silently returns the whole instance. `Users.get_users`
    reads `if user_ids:` (`models/users.py:453`), and an empty list is falsy, so an
    empty scope filters NOTHING. The guard that catches that (`:448-451`) arms only
    when `user_ids` and `group_ids` are BOTH lists; leave `group_ids` out and it is
    `None`, `isinstance` fails, and the guard is skipped.

    So this is the second of two independent barriers. The first is
    `resolve_dashboard_scope` refusing an empty scope outright. Two, because the
    behaviour above belongs to a function in another file that this ticket does not
    own, and one tidy-up there would take the other barrier with it.

    `sorted`, not the set itself: iteration order of a set of strings varies
    between processes, and a filter that reorders between runs makes both the SQL
    and any failing test harder to read than they need to be.
    """
    return {'user_ids': sorted(scope.ids), 'group_ids': []}


#: The same key, deliberately looser: case-folded and stripped of ALL whitespace,
#: not just the edges. Used only to notice near misses - see `scope_metric_rows`.
def _loose_user_key(value: Optional[str]) -> str:
    return ''.join((value or '').split()).casefold()


def scope_metric_rows(
    rows: list, scope: Optional[TeamIdentities], team_id: Optional[str] = None
) -> list:
    """Keep only the Langfuse rows belonging to one team.

    `scope=None` means the unscoped view and returns `rows` unchanged, so the call
    site has no branch of its own to get wrong.

    A row is kept when its normalised key is one of the team's - which covers BOTH
    member emails and member ids, because the frontend claims a row under either
    (`usersAccess.ts:169-179`). `"(unknown)"` (`langfuse/metrics.py:132`) belongs to
    nobody and is dropped: unattributed spend is by definition not the team's.

    ⚠️ **Near misses are counted and logged.** A plain count of dropped rows would
    be useless under scoping - most drops are legitimately other teams' - so what
    is counted instead is rows that match a member under `_loose_user_key` but not
    under `normalize_user_key`. Under a correct implementation that number is
    ALWAYS zero, so any other value is an unambiguous alarm that the two
    normalisations have drifted apart. That drift is the one failure mode which
    does not raise: it silently drops rows on a screen claiming to be exhaustive,
    and in the owner's scoped view it vanishes from both sides of the
    reconciliation, leaving the arithmetic green.

    ⚠️ The log carries a count and a team id, NEVER a key. A Langfuse key is an
    email.
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
