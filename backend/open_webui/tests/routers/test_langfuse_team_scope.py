"""G-A3 - scoping `GET /api/v1/langfuse/metrics` to one team.

⚠️ This route changed from `get_admin_user` to `get_verified_user`. Everything
that used to be enforced by the dependency is now enforced by the first line of
the body, so these tests exercise that line from every side: no `team_id`, wrong
`team_id`, empty team, and the admin path that must stay bit-identical.

`asyncio.run` rather than `@pytest.mark.asyncio`, matching `test_team_scope.py`
and the other PII test files: no plugin in the path, so the file cannot start
reporting green because a dependency went missing.
"""

import asyncio
import logging
import sys
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

sys.modules.setdefault("stripe", MagicMock())

from open_webui.routers import langfuse as route_mod
from open_webui.routers.langfuse import get_langfuse_metrics
from open_webui.utils.auth import get_admin_user, get_verified_user

TEAMS = "open_webui.models.billing.Teams.get_by_id"
MEMBERS = "open_webui.models.billing.TeamMembers.get_by_team_id"
USERS = "open_webui.models.users.Users.get_users_by_user_ids"
WINDOW = "open_webui.routers.langfuse.get_last_week"

ANA, BOJAN, OUTSIDER = "ana@x.com", "bojan@x.com", "zed@other.com"


def _caller(role="user", user_id="u1"):
    return type("C", (), {"role": role, "id": user_id})()


def _team(owner_user_id, team_id="T1"):
    return type("T", (), {"id": team_id, "owner_user_id": owner_user_id})()


def _member(user_id):
    return type("M", (), {"user_id": user_id})()


def _user(user_id, email):
    return type("U", (), {"id": user_id, "email": email})()


def _row(user, cost=1.0):
    return {"user": user, "model": "m", "tokens": 10, "cost": cost, "observations": 1}


DEFAULT_ROWS = [_row(ANA), _row(BOJAN), _row(OUTSIDER), _row("(unknown)")]
TEAM_MEMBERS = [_member("u1"), _member("u2")]
TEAM_USERS = [_user("u1", ANA), _user("u2", BOJAN)]


def _call(caller, team_id=None, rows=None, team=None, members=None, users=None):
    """Invoke the route handler directly and report what each model was asked.

    Returns `(response, counts)` where counts are await counts per model read, so
    the query cost is measured rather than estimated.
    """
    window = MagicMock(return_value=("FROM", "TO", list(DEFAULT_ROWS if rows is None else rows)))
    teams = AsyncMock(return_value=team)
    members_mock = AsyncMock(return_value=list(TEAM_MEMBERS if members is None else members))
    users_mock = AsyncMock(return_value=list(TEAM_USERS if users is None else users))

    with patch(WINDOW, window), patch(TEAMS, teams), patch(MEMBERS, members_mock), patch(
        USERS, users_mock
    ):
        response = asyncio.run(
            get_langfuse_metrics(period="week", days=None, team_id=team_id, user=caller, db=None)
        )
    counts = {
        "teams": teams.await_count,
        "members": members_mock.await_count,
        "users": users_mock.await_count,
        "langfuse": window.call_count,
    }
    return response, counts


def _keys(response):
    return sorted(r.user for r in response.rows)


@contextmanager
def collected_warnings():
    """Collect this module's warnings without pytest's fixture - no plugin needed."""
    records = []

    class _Handler(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = _Handler()
    logger = logging.getLogger("open_webui.utils.team_scope")
    logger.addHandler(handler)
    try:
        yield records
    finally:
        logger.removeHandler(handler)


# ---------------------------------------------------------------------------
# The gate that moved from the dependency into the body
# ---------------------------------------------------------------------------


def test_metrics_route_now_depends_on_get_verified_user():
    """Inspects the real dependency graph, like `test_retrieval_billing_gate.py`."""
    calls = []
    for route in route_mod.router.routes:
        if getattr(route, "path", None) == "/metrics":
            calls += [d.call for d in route.dependant.dependencies if d.name == "user"]
    assert calls == [get_verified_user]
    assert get_admin_user not in calls


def test_admin_without_team_id_sees_every_row_and_touches_no_table():
    response, counts = _call(_caller("admin", "adm"))
    assert _keys(response) == sorted([ANA, BOJAN, OUTSIDER, "(unknown)"])
    assert (counts["teams"], counts["members"], counts["users"]) == (0, 0, 0)


def test_non_admin_without_team_id_is_refused():
    with pytest.raises(HTTPException) as e:
        _call(_caller("user", "u1"))
    assert e.value.status_code == 401


def test_non_admin_without_team_id_never_reaches_langfuse():
    """The refusal happens before the window is fetched, not after.

    ⚠️ Asserting on the mock, not merely re-raising: a test whose only statement is
    `pytest.raises` around a call that already raises cannot fail for the reason it
    claims, and would keep passing with the guard moved after the fetch.
    """
    window = MagicMock(return_value=("FROM", "TO", list(DEFAULT_ROWS)))
    with patch(WINDOW, window):
        with pytest.raises(HTTPException):
            asyncio.run(
                get_langfuse_metrics(period="week", days=None, team_id=None,
                                     user=_caller("user", "u1"), db=None)
            )
    window.assert_not_called()


def test_owner_of_another_team_is_refused():
    with pytest.raises(HTTPException) as e:
        _call(_caller("user", "u1"), team_id="T2", team=_team("u2", team_id="T2"))
    assert e.value.status_code == 401


def test_empty_team_is_refused_and_langfuse_is_never_called():
    with pytest.raises(HTTPException) as e:
        _call(_caller("user", "u1"), team_id="T1", team=_team("u1"), members=[], users=[])
    assert e.value.status_code == 401


def test_empty_team_refusal_precedes_the_langfuse_request():
    window = MagicMock(return_value=("FROM", "TO", list(DEFAULT_ROWS)))
    with patch(WINDOW, window), patch(TEAMS, AsyncMock(return_value=_team("u1"))), patch(
        MEMBERS, AsyncMock(return_value=[])
    ), patch(USERS, AsyncMock(return_value=[])):
        with pytest.raises(HTTPException):
            asyncio.run(
                get_langfuse_metrics(period="week", days=None, team_id="T1",
                                     user=_caller("user", "u1"), db=None)
            )
    window.assert_not_called()


# ---------------------------------------------------------------------------
# What the owner actually sees
# ---------------------------------------------------------------------------


def test_owner_sees_only_their_teams_rows():
    response, _ = _call(_caller("user", "u1"), team_id="T1", team=_team("u1"))
    assert _keys(response) == sorted([ANA, BOJAN])


def test_owner_costs_three_model_reads_and_one_langfuse_call():
    """Measured, not estimated: the route used to touch no table at all."""
    _, counts = _call(_caller("user", "u1"), team_id="T1", team=_team("u1"))
    assert counts == {"teams": 1, "members": 1, "users": 1, "langfuse": 1}


def test_admin_may_scope_to_any_team():
    response, _ = _call(_caller("admin", "adm"), team_id="T1", team=_team("someone-else"))
    assert _keys(response) == sorted([ANA, BOJAN])


def test_a_row_recorded_against_the_owui_id_is_kept():
    """The frontend claims a row by email OR id, so the filter must be as wide."""
    response, _ = _call(
        _caller("user", "u1"), team_id="T1", team=_team("u1"),
        rows=[_row("u2"), _row(OUTSIDER)],
    )
    assert _keys(response) == ["u2"]


def test_unknown_is_dropped():
    response, _ = _call(
        _caller("user", "u1"), team_id="T1", team=_team("u1"),
        rows=[_row("(unknown)"), _row(ANA)],
    )
    assert _keys(response) == [ANA]


def test_a_key_differing_only_in_case_and_edge_space_is_kept():
    response, _ = _call(
        _caller("user", "u1"), team_id="T1", team=_team("u1"),
        rows=[_row("  ANA@X.COM ")],
    )
    assert len(response.rows) == 1


# ---------------------------------------------------------------------------
# The near-miss alarm
# ---------------------------------------------------------------------------


def test_a_row_matching_only_under_the_loose_key_is_dropped_and_counted(caplog):
    """Internal whitespace: `trim` keeps it, so this is NOT the same identity.

    It is the shape a drifted normalisation produces, and the only signal that the
    two sides have stopped agreeing.
    """
    with caplog.at_level("WARNING"):
        response, _ = _call(
            _caller("user", "u1"), team_id="T1", team=_team("u1"),
            rows=[_row("ana@ x.com")],
        )
    assert response.rows == []
    hits = [r.getMessage() for r in caplog.records if "loose key" in r.getMessage()]
    assert len(hits) == 1
    assert "1 row(s)" in hits[0]


def test_the_near_miss_warning_never_carries_the_key(caplog):
    """A Langfuse key is an email. Counts and a team id leave; values do not."""
    with caplog.at_level("WARNING"):
        _call(_caller("user", "u1"), team_id="T1", team=_team("u1"), rows=[_row("ana@ x.com")])
    hits = [r.getMessage() for r in caplog.records if "loose key" in r.getMessage()]
    assert hits and "@" not in hits[0] and "ana" not in hits[0]


def test_no_warning_when_nothing_is_near():
    with collected_warnings() as records:
        _call(_caller("user", "u1"), team_id="T1", team=_team("u1"))
    assert not [r for r in records if "loose key" in r]
