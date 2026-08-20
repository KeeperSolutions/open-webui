"""Tests for team identity resolution — TRAU-D1 level A, gate G-A1.

Scope: `normalize_user_key` and `resolve_team_identities`. NO routes, no
`Depends`, no shared preamble — those are G-A2b, G-A3 and G-A4.

⚠️ The normalisation tests use TWO corpora with DIFFERENT jobs, and they are not
interchangeable:

  * **literals** carry the discriminating power. Every class of difference is
    represented by one hand-written expectation.
  * **the real key set** carries shape only. Measured on the development
    database: 34 distinct keys, of which **0** change under normalisation — so an
    implementation with no lower-casing at all passes that corpus green. It is
    here to surface a class the literals do not model, not to catch a regression.

The other side of the equivalence is pinned by its own literals, in vitest:
`costAnalytics.test.ts:57-58` (case + edge whitespace fold into one bar),
`costAnalytics.test.ts:186-187` (case-insensitive match),
`usersAccess.test.ts:72` (folds case and whitespace variants into one key),
`usersAccess.test.ts:106-107` (`'  ANA@X.COM '` attributes to `ana@x.com`).
Those cases are mirrored below on purpose: neither side is tested against the
other, so both are tested against the same stated answers.

Exotic code points are written as `chr(...)`, never as literals: a raw U+001F in
a source file is invisible to every reviewer who would need to see it.
"""

import asyncio
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from open_webui.utils.team_scope import TeamIdentities, normalize_user_key

# `resolve_team_identities` reaches the models, and importing those initialises
# the async engine. Probed once here so a broken environment produces a LOUD skip
# naming the missing package, never a quiet pass.
try:  # pragma: no cover - environment probe
    import open_webui.models.billing  # noqa: F401
    import open_webui.models.users  # noqa: F401

    MODELS_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - environment probe
    MODELS_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"

needs_models = pytest.mark.skipif(
    MODELS_IMPORT_ERROR is not None,
    reason=f"backend environment cannot import the models: {MODELS_IMPORT_ERROR}",
)

BOM = chr(0xFEFF)       # trim removes it; str.strip() does not
NBSP = chr(0x00A0)      # whitespace to both
UNIT_SEP = chr(0x001F)  # str.strip() removes it; trim does not
NEL = chr(0x0085)       # str.strip() removes it; trim does not


# ---------------------------------------------------------------------------
# Corpus 1 - literals. All of the discriminating power lives here.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected,why",
    [
        ("A@x.com", "a@x.com", "case folds - mirrors costAnalytics.test.ts:57-58"),
        (" a@x.com ", "a@x.com", "edge whitespace - mirrors costAnalytics.test.ts:57-58"),
        ("  ANA@X.COM ", "ana@x.com", "both - mirrors usersAccess.test.ts:106-107"),
        ("A@X.COM", "a@x.com", "all caps - mirrors costAnalytics.test.ts:186-187"),
        ("a b@x.com", "a b@x.com", "INTERNAL whitespace is preserved, as trim preserves it"),
        ("\ta@x.com\n", "a@x.com", "tab and newline are whitespace to trim"),
        (NBSP + "a@x.com" + NBSP, "a@x.com", "NBSP is whitespace to both sides"),
        (BOM + "a@x.com", "a@x.com", "trim removes U+FEFF; str.strip() does NOT"),
        (UNIT_SEP + "a", UNIT_SEP + "a", "str.strip() removes U+001F; trim does NOT"),
        (NEL + "a", NEL + "a", "str.strip() removes U+0085; trim does NOT"),
        ("", "", "empty stays empty"),
        ("   ", "", "whitespace-only collapses to empty"),
        (None, "", "missing value is the empty key, never a crash"),
    ],
)
def test_normalize_user_key_literals(raw, expected, why):
    assert normalize_user_key(raw) == expected, why


def test_normalize_user_key_is_idempotent():
    for raw in ("A@x.com", " a@x.com ", BOM + "A@X.COM", "a b@x.com", ""):
        once = normalize_user_key(raw)
        assert normalize_user_key(once) == once


# ---------------------------------------------------------------------------
# Corpus 2 - the real key set. Shape only.
# ---------------------------------------------------------------------------

_DEV_DB = Path(__file__).resolve().parents[2] / "data" / "webui.db"


def _distinct_ledger_keys():
    """Every distinct Langfuse identity the ledger has seen, or None.

    Read-only, stdlib `sqlite3`, no SQLAlchemy and therefore no async driver: this
    corpus must stay readable even when the backend environment cannot import the
    models.
    """
    if not _DEV_DB.exists():
        return None
    con = sqlite3.connect(f"file:{_DEV_DB}?mode=ro", uri=True)
    try:
        con.execute("SELECT 1 FROM usage_ledger LIMIT 1")
        return [row[0] for row in con.execute("SELECT DISTINCT user_id FROM usage_ledger")]
    except sqlite3.DatabaseError:
        return None
    finally:
        con.close()


def _looks_like_uuid(value: str) -> bool:
    parts = value.split("-")
    return len(parts) == 5 and all(
        p and all(c in "0123456789abcdefABCDEF" for c in p) for p in parts
    )


def test_real_key_set_holds_no_class_the_literals_miss():
    """A key shaped unlike anything above is a finding, not a pass.

    Values are never asserted on, printed, or committed: a Langfuse key is an
    email, which is personal data. Only counts and classes leave this test.
    """
    keys = _distinct_ledger_keys()
    if keys is None:
        pytest.skip(
            f"no readable usage_ledger at {_DEV_DB}; shape UNVERIFIED, not verified-empty"
        )
    assert keys, "usage_ledger is empty: this corpus proves nothing, so do not read it as green"

    unmodelled = [
        k for k in keys if k not in (None, "") and "@" not in k and not _looks_like_uuid(k)
    ]
    assert not unmodelled, (
        f"{len(unmodelled)} of {len(keys)} distinct keys match no class the literals model "
        "(empty / email-shaped / uuid-shaped). Add a literal case for the new class first."
    )

    non_ascii = [k for k in keys if k and not k.isascii()]
    assert not non_ascii, (
        f"{len(non_ascii)} of {len(keys)} keys are non-ASCII. Python str.lower() and JS "
        "toLowerCase() agree on ASCII but not everywhere; the equivalence needs re-checking."
    )

    for k in keys:
        once = normalize_user_key(k)
        assert normalize_user_key(once) == once


# ---------------------------------------------------------------------------
# `resolve_team_identities`
# ---------------------------------------------------------------------------

MEMBERS = "open_webui.models.billing.TeamMembers.get_by_team_id"
USERS = "open_webui.models.users.Users.get_users_by_user_ids"


def _member(user_id):
    return type("M", (), {"user_id": user_id})()


def _user(user_id, email):
    return type("U", (), {"id": user_id, "email": email})()


def _resolve(team_id, members, users):
    """Run the resolver and hand back the two mocks it was given.

    ⚠️ `asyncio.run`, not `@pytest.mark.asyncio`. That marker needs the
    `pytest-asyncio` plugin, and when the plugin is absent pytest does not fail —
    it warns, never awaits the coroutine, and reports the test as PASSED. Every
    other PII test file here uses the same helper shape for the same reason
    (`test_pii_toggle.py:84`, `test_pii_fail_closed.py:45`).
    """
    from open_webui.utils.team_scope import resolve_team_identities

    with patch(MEMBERS, new=AsyncMock(return_value=members)) as m, patch(
        USERS, new=AsyncMock(return_value=users)
    ) as u:
        return asyncio.run(resolve_team_identities(team_id)), m, u


@needs_models
def test_three_members_yield_ids_and_both_key_kinds():
    out, _, _ = _resolve(
        "T1",
        [_member("u1"), _member("u2"), _member("u3")],
        [_user("u1", "a@x.com"), _user("u2", "b@x.com"), _user("u3", "c@x.com")],
    )
    assert out.ids == {"u1", "u2", "u3"}
    assert out.keys == {"u1", "u2", "u3", "a@x.com", "b@x.com", "c@x.com"}


@needs_models
def test_duplicate_membership_row_counts_once():
    """A set, not a list. Twice in `team_members` must not mean twice in the filter."""
    out, _, users_mock = _resolve(
        "T1", [_member("u1"), _member("u1")], [_user("u1", "a@x.com")]
    )
    assert out.ids == {"u1"}
    assert len(out.ids) == 1
    assert users_mock.await_args.args[0] == ["u1"]


@needs_models
def test_empty_email_contributes_no_key():
    out, _, _ = _resolve("T1", [_member("u1")], [_user("u1", "")])
    assert "" not in out.keys
    assert out.keys == {"u1"}


@needs_models
def test_keys_are_normalised():
    out, _, _ = _resolve("T1", [_member("u1")], [_user("u1", "  ANA@X.COM ")])
    assert out.keys == {"u1", "ana@x.com"}


@needs_models
def test_team_without_members_asks_nothing_further():
    """The directory is never queried on an empty scope - not even with an empty list."""
    out, _, users_mock = _resolve("T1", [], [])
    assert out == TeamIdentities(frozenset(), frozenset())
    users_mock.assert_not_awaited()


@needs_models
def test_unknown_team_is_the_same_as_an_empty_one():
    out, _, users_mock = _resolve("nope", [], [])
    assert out.ids == frozenset()
    assert out.keys == frozenset()
    users_mock.assert_not_awaited()


@needs_models
def test_membership_pointing_at_a_deleted_account_drops_the_id(caplog):
    out, _, _ = _resolve(
        "T1", [_member("u1"), _member("ghost")], [_user("u1", "a@x.com")]
    )
    assert out.ids == {"u1"}
    assert "ghost" not in out.keys
    assert any("have no user record" in r.getMessage() for r in caplog.records)


@needs_models
def test_both_fields_are_sets():
    out, _, _ = _resolve("T1", [_member("u1")], [_user("u1", "a@x.com")])
    assert isinstance(out.ids, frozenset)
    assert isinstance(out.keys, frozenset)


# ---------------------------------------------------------------------------
# G-A2 - `_may_read_team_dashboard`
# ---------------------------------------------------------------------------

TEAMS = "open_webui.models.billing.Teams.get_by_id"


def _caller(role="user", user_id="u1"):
    return type("C", (), {"role": role, "id": user_id})()


def _team(owner_user_id, team_id="T1"):
    return type("T", (), {"id": team_id, "owner_user_id": owner_user_id})()


def _may(caller, team_id, team):
    """Run the guard with the caller present in `team_members` of the team asked for.

    ⚠️ The membership mock is the point, not scaffolding. The guard must refuse a
    member who does not own the team, and a test where no membership exists cannot
    tell "membership was ignored" from "there was nothing to ignore". Patching it
    also stops a mutated guard from reaching the real database and dying of that
    instead of of the assertion.
    """
    from open_webui.utils.team_scope import _may_read_team_dashboard

    with patch(TEAMS, new=AsyncMock(return_value=team)), patch(
        MEMBERS, new=AsyncMock(return_value=[_member(caller.id)])
    ):
        return asyncio.run(_may_read_team_dashboard(caller, team_id))


@needs_models
def test_admin_without_any_team_may_read_someone_elses():
    assert _may(_caller("admin", "adm"), "T1", _team("someone-else")) is True


@needs_models
def test_owner_may_read_their_own_team():
    assert _may(_caller("user", "u1"), "T1", _team("u1")) is True


@needs_models
def test_owner_of_one_team_may_not_read_another():
    assert _may(_caller("user", "u1"), "T2", _team("u2", team_id="T2")) is False


@needs_models
def test_member_who_does_not_own_the_team_may_not_read_it():
    assert _may(_caller("user", "u9"), "T1", _team("u1")) is False


@needs_models
def test_team_members_role_owner_does_not_grant_access():
    """`team_members.role` is not consulted, so a stale 'owner' row grants nothing.

    Both ownership records are written at team creation and nothing keeps them in
    step. This pins which one is the answer.
    """
    assert _may(_caller("user", "u9"), "T1", _team("u1")) is False


@needs_models
def test_owner_with_no_team_members_row_still_passes():
    """Authorisation must not depend on a table it never asks about."""
    assert _may(_caller("user", "u1"), "T1", _team("u1")) is True


@needs_models
def test_unknown_team_is_refused_without_raising():
    assert _may(_caller("user", "u1"), "nope", None) is False


@needs_models
def test_admin_is_allowed_even_when_the_team_does_not_exist():
    """The admin branch short-circuits: no lookup, so nothing to be wrong about."""
    assert _may(_caller("admin", "adm"), "nope", None) is True


# ---------------------------------------------------------------------------
# G-A2b - `resolve_dashboard_scope` and `team_directory_filter`
# ---------------------------------------------------------------------------


def _scope(caller, team_id, team=None, members=(), users=()):
    """Run the whole preamble against mocked models, returning what it returned."""
    from open_webui.utils.team_scope import resolve_dashboard_scope

    with patch(TEAMS, new=AsyncMock(return_value=team)), patch(
        MEMBERS, new=AsyncMock(return_value=list(members))
    ) as m, patch(USERS, new=AsyncMock(return_value=list(users))) as u:
        return asyncio.run(resolve_dashboard_scope(caller, team_id)), m, u


@needs_models
def test_no_team_id_and_admin_means_no_scoping():
    out, members_mock, users_mock = _scope(_caller("admin", "adm"), None)
    assert out is None
    members_mock.assert_not_awaited()
    users_mock.assert_not_awaited()


@needs_models
def test_no_team_id_and_not_admin_is_refused():
    """The `get_admin_user` dependency did not disappear; it moved here."""
    with pytest.raises(HTTPException) as e:
        _scope(_caller("user", "u1"), None)
    assert e.value.status_code == 401


@needs_models
def test_someone_elses_team_is_refused_before_any_member_is_read():
    """⚠️ The other team must have MEMBERS, or this test proves nothing.

    With an empty team, removing the authorisation check entirely still produces a
    401 — the empty-scope barrier catches it, and the test passes for a reason it
    never meant to assert. Populating T2 leaves the guard as the only thing that
    can refuse, and `assert_not_awaited` pins that it refuses BEFORE reading them.
    """
    with pytest.raises(HTTPException) as e:
        _scope(
            _caller("user", "u1"), "T2", team=_team("u2", team_id="T2"),
            members=[_member("u2"), _member("u3")],
            users=[_user("u2", "b@x.com"), _user("u3", "c@x.com")],
        )
    assert e.value.status_code == 401


@needs_models
def test_refusal_for_someone_elses_team_reads_no_members_at_all():
    from open_webui.utils.team_scope import resolve_dashboard_scope

    with patch(TEAMS, new=AsyncMock(return_value=_team("u2", team_id="T2"))), patch(
        MEMBERS, new=AsyncMock(return_value=[_member("u2")])
    ) as members_mock, patch(
        USERS, new=AsyncMock(return_value=[_user("u2", "b@x.com")])
    ) as users_mock:
        with pytest.raises(HTTPException):
            asyncio.run(resolve_dashboard_scope(_caller("user", "u1"), "T2"))
    members_mock.assert_not_awaited()
    users_mock.assert_not_awaited()


@needs_models
def test_owner_of_the_team_gets_its_scope():
    out, _, _ = _scope(
        _caller("user", "u1"), "T1", team=_team("u1"),
        members=[_member("u1"), _member("u2")],
        users=[_user("u1", "a@x.com"), _user("u2", "b@x.com")],
    )
    assert out.ids == {"u1", "u2"}
    assert out.keys == {"u1", "u2", "a@x.com", "b@x.com"}


@needs_models
def test_admin_gets_the_scope_of_any_team():
    out, _, _ = _scope(
        _caller("admin", "adm"), "T1", team=_team("someone-else"),
        members=[_member("u1")], users=[_user("u1", "a@x.com")],
    )
    assert out.ids == {"u1"}


@needs_models
def test_empty_scope_is_refused_and_nothing_downstream_is_queried():
    """An empty scope never reaches a caller, because an empty filter filters nothing."""
    with pytest.raises(HTTPException) as e:
        _scope(_caller("user", "u1"), "T1", team=_team("u1"), members=[], users=[])
    assert e.value.status_code == 401


@needs_models
def test_scope_is_refused_when_every_member_account_is_gone():
    """Membership rows survive their accounts; a scope of ghosts is still empty."""
    with pytest.raises(HTTPException) as e:
        _scope(_caller("user", "u1"), "T1", team=_team("u1"),
               members=[_member("ghost")], users=[])
    assert e.value.status_code == 401


@needs_models
def test_directory_filter_carries_both_keys():
    from open_webui.utils.team_scope import TeamIdentities, team_directory_filter

    out = team_directory_filter(TeamIdentities(frozenset({"u2", "u1"}), frozenset()))
    assert out == {"user_ids": ["u1", "u2"], "group_ids": []}
    assert isinstance(out["user_ids"], list) and isinstance(out["group_ids"], list)


# ---------------------------------------------------------------------------
# The fail-open this ticket does not own
# ---------------------------------------------------------------------------


@needs_models
def test_users_get_users_still_returns_the_whole_instance_for_an_empty_user_ids():
    """⚠️ A test of SOMEONE ELSE'S behaviour, on purpose.

    `Users.get_users` reads `if user_ids:` (`models/users.py:453`), so an empty
    list filters nothing and the call returns every account on the instance. The
    guard that would catch it (`:448-451`) arms only when `user_ids` AND
    `group_ids` are both lists.

    This exists so `team_directory_filter`'s `group_ids: []` is never removed as
    padding. **When this test starts failing, someone has fixed
    `models/users.py`** — that is a finding to report, not a break to repair here.
    """
    import time
    from contextlib import asynccontextmanager

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from open_webui.models.users import User, UsersTable

    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(User.__table__.create, checkfirst=True)
        session = async_sessionmaker(bind=engine, expire_on_commit=False)()
        now = int(time.time())
        for i in range(3):
            session.add(
                User(id=f"u{i}", name=f"U{i}", email=f"u{i}@x.com", role="user",
                     created_at=now + i, updated_at=now + i, last_active_at=now + i)
            )
        await session.commit()

        @asynccontextmanager
        async def _ctx(db=None):
            yield session

        # ⚠️ Patching the context manager is required, not cosmetic:
        # `DATABASE_ENABLE_SESSION_SHARING` is off, so `get_async_db_context`
        # ignores the session it is handed and opens a real one. The same trap is
        # documented at `tests/models/test_user_locate.py:74-79`.
        with patch("open_webui.models.users.get_async_db_context", _ctx):
            table = UsersTable()
            unguarded = await table.get_users(filter={"user_ids": []}, db=session)
            guarded = await table.get_users(
                filter={"user_ids": [], "group_ids": []}, db=session
            )
        await session.close()
        await engine.dispose()
        return unguarded, guarded

    unguarded, guarded = asyncio.run(scenario())
    assert len(unguarded["users"]) == 3, (
        "the fail-open is gone - models/users.py:453 was fixed. Report it; "
        "team_directory_filter's group_ids may now be redundant."
    )
    assert len(guarded["users"]) == 0
