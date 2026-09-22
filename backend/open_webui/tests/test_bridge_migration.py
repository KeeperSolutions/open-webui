"""Tests for the migration that moves existing teams onto their own policy group.

The main risks are someone ending up outside every enforcing group, and a
decision being made against rows the migration already wrote.

Each test uses an in-memory SQLite database with a minimal schema, patches the
migration's `op` so `get_bind()` returns that connection, and calls `upgrade()`
directly. The connection is wrapped in `Recorder` so tests can assert statement
order, which the final rows do not show.
"""

import importlib
import json
import pathlib
import sys
import time
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, text

sys.modules.setdefault("stripe", MagicMock())

MIGRATION = "open_webui.migrations.versions.b6d1a4f0c7e2_bridge_team_pii_groups"
bridge = importlib.import_module(MIGRATION)

SOURCE = bridge.SOURCE_GROUP_ID

TEAM_A, TEAM_B = "team-a", "team-b"
A1, A2, B1, LONER = "u-a1", "u-a2", "u-b1", "u-loner"
CUSTOM_GROUP = "g-custom-policy"
ENFORCING = {"chat": {"pii_masking_enforced": True}}


class Recorder:
    """A connection that remembers what was executed, in order."""

    def __init__(self, conn):
        self._conn = conn
        self.log = []

    def execute(self, statement, parameters=None, *args, **kwargs):
        self.log.append((str(statement), parameters))
        if parameters is None:
            return self._conn.execute(statement, *args, **kwargs)
        return self._conn.execute(statement, parameters, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def membership_events(self):
        """Every group_member write, flattened to (kind, group_id, user_id), in order."""
        events = []
        for statement, parameters in self.log:
            collapsed = " ".join(statement.split()).upper()
            if "GROUP_MEMBER" not in collapsed:
                continue
            if collapsed.startswith("INSERT"):
                kind = "add"
            elif collapsed.startswith("DELETE"):
                kind = "remove"
            else:
                continue
            rows = parameters if isinstance(parameters, list) else [parameters]
            for row in rows:
                if isinstance(row, dict) and "user_id" in row:
                    events.append((kind, row["group_id"], row["user_id"]))
                elif isinstance(row, dict) and "g" in row:
                    # downgrade's bulk delete carries the group only
                    events.append((kind, row["g"], None))
        return events


SCHEMA = [
    """CREATE TABLE teams (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        group_id TEXT
    )""",
    "CREATE UNIQUE INDEX uq_teams_group_id ON teams (group_id)",
    """CREATE TABLE team_members (
        id TEXT PRIMARY KEY,
        team_id TEXT NOT NULL,
        user_id TEXT NOT NULL
    )""",
    "CREATE UNIQUE INDEX uq_team_members_user_id ON team_members (user_id)",
    """CREATE TABLE "group" (
        id TEXT PRIMARY KEY,
        user_id TEXT,
        name TEXT,
        description TEXT,
        data JSON,
        meta JSON,
        permissions JSON,
        created_at BIGINT,
        updated_at BIGINT
    )""",
    """CREATE TABLE group_member (
        id TEXT PRIMARY KEY,
        group_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        created_at BIGINT,
        updated_at BIGINT,
        CONSTRAINT uq_group_member_group_user UNIQUE (group_id, user_id)
    )""",
    """CREATE TABLE pii_policy_audit (
        id TEXT PRIMARY KEY,
        event_type TEXT NOT NULL,
        group_id TEXT NOT NULL,
        user_id TEXT,
        actor_user_id TEXT NOT NULL,
        actor_email TEXT NOT NULL,
        reason TEXT,
        event_ts BIGINT NOT NULL
    )""",
]


def _add_group(conn, group_id, name, permissions):
    conn.execute(
        text(
            'INSERT INTO "group" (id, user_id, name, description, data, meta, '
            "permissions, created_at, updated_at) VALUES "
            "(:id, '', :name, '', '{}', NULL, :permissions, 0, 0)"
        ),
        {"id": group_id, "name": name, "permissions": json.dumps(permissions)},
    )


def _add_member(conn, group_id, user_id):
    conn.execute(
        text(
            "INSERT INTO group_member (id, group_id, user_id, created_at, updated_at) "
            "VALUES (:id, :g, :u, 0, 0)"
        ),
        {"id": f"gm-{group_id}-{user_id}", "g": group_id, "u": user_id},
    )


@pytest.fixture
def conn():
    """Two teams (one with two members), a team-less user, the seeded group and a custom enforcing group.

    Two teams and two members per team are needed to expose order-dependent
    bugs that a single team or member would hide.
    """
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as raw:
        for statement in SCHEMA:
            raw.execute(text(statement))

        raw.execute(text("INSERT INTO teams VALUES ('team-a', 'Alpha', NULL)"))
        raw.execute(text("INSERT INTO teams VALUES ('team-b', 'Beta', NULL)"))
        for i, (team, user) in enumerate(
            [(TEAM_A, A1), (TEAM_A, A2), (TEAM_B, B1)]
        ):
            raw.execute(
                text("INSERT INTO team_members VALUES (:id, :t, :u)"),
                {"id": f"tm-{i}", "t": team, "u": user},
            )

        _add_group(raw, SOURCE, "PII Masking Policy", ENFORCING)
        for user in (A1, A2, B1, LONER):
            _add_member(raw, SOURCE, user)

        # Enforcing, but not the seeded one and not any team's. Nothing this
        # migration does may reach it.
        _add_group(raw, CUSTOM_GROUP, "Legal hold", ENFORCING)
        _add_member(raw, CUSTOM_GROUP, LONER)

        raw.commit()
        yield Recorder(raw)


def _upgrade(conn):
    with patch.object(bridge, "op") as mock_op:
        mock_op.get_bind.return_value = conn
        bridge.upgrade()
    conn.commit()


def _downgrade(conn):
    with patch.object(bridge, "op") as mock_op:
        mock_op.get_bind.return_value = conn
        bridge.downgrade()
    conn.commit()


def _team_group(conn, team_id):
    return conn.execute(
        text("SELECT group_id FROM teams WHERE id = :t"), {"t": team_id}
    ).scalar()


def _members(conn, group_id):
    return {
        uid
        for (uid,) in conn.execute(
            text("SELECT user_id FROM group_member WHERE group_id = :g"), {"g": group_id}
        ).fetchall()
    }


def _audit(conn, **where):
    clauses = " AND ".join(f"{k} = :{k}" for k in where) or "1=1"
    return conn.execute(
        text(f"SELECT * FROM pii_policy_audit WHERE {clauses}"), where
    ).fetchall()


# ---------------------------------------------------------------------------
# One group per existing team
# ---------------------------------------------------------------------------


def test_every_team_gets_its_own_group(conn):
    _upgrade(conn)
    a, b = _team_group(conn, TEAM_A), _team_group(conn, TEAM_B)
    assert a and b and a != b
    assert {a, b}.isdisjoint({SOURCE, CUSTOM_GROUP})


def test_the_group_carries_only_the_masking_key(conn):
    _upgrade(conn)
    permissions = conn.execute(
        text('SELECT permissions FROM "group" WHERE id = :g'),
        {"g": _team_group(conn, TEAM_A)},
    ).scalar()
    assert json.loads(permissions) == bridge.TEAM_PII_GROUP_PERMISSIONS


# ---------------------------------------------------------------------------
# Members move from the seeded group to the team group
# ---------------------------------------------------------------------------


def test_team_members_move(conn):
    _upgrade(conn)
    assert _members(conn, _team_group(conn, TEAM_A)) == {A1, A2}
    assert _members(conn, _team_group(conn, TEAM_B)) == {B1}
    assert _members(conn, SOURCE) == {LONER}


def test_nobody_is_ever_outside_both_groups(conn):
    """Each moved user is added to the team group before removal from the seeded group.

    Checked from the statement log, because both orders end in the same rows and
    the wrong order leaves the user briefly unmasked.
    """
    _upgrade(conn)
    events = conn.membership_events()

    for user in (A1, A2, B1):
        added = [i for i, (kind, _, uid) in enumerate(events) if kind == "add" and uid == user]
        removed = [
            i
            for i, (kind, gid, uid) in enumerate(events)
            if kind == "remove" and uid == user and gid == SOURCE
        ]
        assert added and removed, (user, events)
        assert min(added) < min(removed), (
            f"{user} was taken out of {SOURCE} at statement {min(removed)} but only "
            f"added to the team group at {min(added)} — a window with no masking"
        )


def test_the_only_group_anyone_is_removed_from_is_the_seeded_one(conn):
    """Every `member_removed` audit row targets the seeded group.

    Choosing sources by the masking flag would also hit custom groups and the
    team groups this migration creates.
    """
    _add_member(conn, CUSTOM_GROUP, A1)
    conn.commit()

    _upgrade(conn)

    removed_from = {row[2] for row in _audit(conn, event_type="member_removed")}
    assert removed_from == {SOURCE}


# ---------------------------------------------------------------------------
# Users without a team are not touched
# ---------------------------------------------------------------------------


def test_a_person_with_no_team_stays_in_the_seeded_group(conn):
    _upgrade(conn)
    assert LONER in _members(conn, SOURCE)
    assert _audit(conn, user_id=LONER) == []


# ---------------------------------------------------------------------------
# A custom enforcing group is not a source
# ---------------------------------------------------------------------------


def test_a_custom_enforcing_group_is_untouched(conn):
    """A custom enforcing group keeps its members and gets no audit rows.

    No team group replaces it, so emptying it would remove masking.
    """
    _upgrade(conn)
    assert _members(conn, CUSTOM_GROUP) == {LONER}
    assert _audit(conn, group_id=CUSTOM_GROUP) == []


def test_a_team_member_who_is_also_in_a_custom_group_keeps_that_membership(conn):
    _add_member(conn, CUSTOM_GROUP, A1)
    conn.commit()
    _upgrade(conn)
    assert A1 in _members(conn, CUSTOM_GROUP)


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------


def test_one_added_and_one_removed_row_per_moved_member(conn):
    _upgrade(conn)
    for user in (A1, A2, B1):
        assert len(_audit(conn, event_type="member_added", user_id=user)) == 1
        assert len(_audit(conn, event_type="member_removed", user_id=user)) == 1


def test_every_row_is_attributed_to_the_system_and_says_why(conn):
    _upgrade(conn)
    rows = conn.execute(
        text("SELECT event_type, actor_user_id, actor_email, reason FROM pii_policy_audit")
    ).fetchall()
    assert rows
    for event_type, actor, email, reason in rows:
        assert actor == bridge.SYSTEM_ACTOR_ID
        assert email == bridge.SYSTEM_ACTOR_EMAIL
        assert (reason or "").strip()
        assert bridge.revision in reason


def test_the_removal_reason_says_masking_did_not_change(conn):
    """The `member_removed` reason states that masking is unchanged, so it does not read as protection withdrawn."""
    _upgrade(conn)
    reason = _audit(conn, event_type="member_removed", user_id=A1)[0][6]
    assert "unchanged" in reason and "team" in reason


def test_every_audit_row_goes_through_the_model_validator(conn):
    """The model validator runs once per written audit row, since raw inserts skip the model."""
    import open_webui.models.pii_policy_audit as audit_model

    seen = []
    real = audit_model.validate_pii_policy_event

    def _counting(**kwargs):
        seen.append(kwargs)
        return real(**kwargs)

    with patch.object(audit_model, "validate_pii_policy_event", _counting):
        _upgrade(conn)

    written = conn.execute(text("SELECT COUNT(*) FROM pii_policy_audit")).scalar()
    assert written > 0
    assert len(seen) == written


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_running_twice_changes_nothing(conn):
    _upgrade(conn)
    first = {
        "groups": conn.execute(text('SELECT COUNT(*) FROM "group"')).scalar(),
        "members": conn.execute(text("SELECT COUNT(*) FROM group_member")).scalar(),
        "audit": conn.execute(text("SELECT COUNT(*) FROM pii_policy_audit")).scalar(),
        "team_a": _team_group(conn, TEAM_A),
        "team_b": _team_group(conn, TEAM_B),
    }

    _upgrade(conn)

    assert conn.execute(text('SELECT COUNT(*) FROM "group"')).scalar() == first["groups"]
    assert conn.execute(text("SELECT COUNT(*) FROM group_member")).scalar() == first["members"]
    assert conn.execute(text("SELECT COUNT(*) FROM pii_policy_audit")).scalar() == first["audit"]
    assert _team_group(conn, TEAM_A) == first["team_a"]
    assert _team_group(conn, TEAM_B) == first["team_b"]


def test_a_removal_that_happens_twice_is_recorded_twice(conn):
    """A user re-added to the seeded group and moved again gets a second `member_removed` row.

    Every membership write must have an audit row.
    """
    _upgrade(conn)
    assert len(_audit(conn, event_type="member_removed", user_id=A1)) == 1

    _add_member(conn, SOURCE, A1)
    conn.commit()

    _upgrade(conn)

    assert A1 not in _members(conn, SOURCE)
    assert len(_audit(conn, event_type="member_removed", user_id=A1)) == 2


def test_a_second_run_writes_no_audit_rows_at_all(conn):
    _upgrade(conn)
    before = conn.execute(text("SELECT COUNT(*) FROM pii_policy_audit")).scalar()
    conn.log.clear()
    _upgrade(conn)
    assert conn.execute(text("SELECT COUNT(*) FROM pii_policy_audit")).scalar() == before
    assert not [s for s, _ in conn.log if "PII_POLICY_AUDIT" in s.upper() and "INSERT" in s.upper()]


# ---------------------------------------------------------------------------
# Empty or missing seeded group
# ---------------------------------------------------------------------------


def test_an_empty_seeded_group_still_gets_every_team_a_group(conn):
    """With an empty seeded group, every team gets a group and nobody is enrolled.

    Enrolling team members here would newly enforce masking on them.
    """
    conn.execute(text("DELETE FROM group_member WHERE group_id = :g"), {"g": SOURCE})
    conn.commit()

    _upgrade(conn)

    assert _team_group(conn, TEAM_A) and _team_group(conn, TEAM_B)
    assert _members(conn, SOURCE) == set()
    assert _members(conn, _team_group(conn, TEAM_A)) == set()
    assert _members(conn, _team_group(conn, TEAM_B)) == set()
    assert _audit(conn, event_type="member_removed") == []
    assert _audit(conn, event_type="member_added") == []


def test_a_team_member_the_seeded_group_does_not_mask_is_left_alone(conn):
    """A team member outside the seeded group is not moved and gets no audit row; the other member is moved."""
    conn.execute(
        text("DELETE FROM group_member WHERE group_id = :g AND user_id = :u"),
        {"g": SOURCE, "u": A2},
    )
    conn.commit()

    _upgrade(conn)

    assert _members(conn, _team_group(conn, TEAM_A)) == {A1}
    assert len(_audit(conn, event_type="member_added", user_id=A1)) == 1
    assert len(_audit(conn, event_type="member_removed", user_id=A1)) == 1
    # A2 was not masked, so it must have no audit rows at all.
    assert _audit(conn, user_id=A2) == []


def test_a_missing_seeded_group_is_not_an_error(conn):
    """Without the seeded group, teams still get groups and nobody is moved.

    The seed migration skips instances that already had an enforcing group.
    """
    conn.execute(text("DELETE FROM group_member WHERE group_id = :g"), {"g": SOURCE})
    conn.execute(text('DELETE FROM "group" WHERE id = :g'), {"g": SOURCE})
    conn.commit()

    _upgrade(conn)

    assert _team_group(conn, TEAM_A) and _team_group(conn, TEAM_B)
    # No seeded group means nobody to move.
    assert _members(conn, _team_group(conn, TEAM_A)) == set()
    assert _audit(conn, event_type="member_removed") == []
    assert _audit(conn, event_type="member_added") == []


def test_a_team_with_no_members_still_gets_a_group(conn):
    conn.execute(text("INSERT INTO teams VALUES ('team-empty', 'Gamma', NULL)"))
    conn.commit()
    _upgrade(conn)
    assert _team_group(conn, "team-empty")


# ---------------------------------------------------------------------------
# Dangling references
# ---------------------------------------------------------------------------


def test_a_dangling_group_id_is_replaced(conn):
    """A `group_id` pointing at a deleted group is replaced; SQLite's `PRAGMA foreign_keys` is off."""
    conn.execute(
        text("UPDATE teams SET group_id = 'gone-for-good' WHERE id = :t"), {"t": TEAM_A}
    )
    conn.commit()

    _upgrade(conn)

    replacement = _team_group(conn, TEAM_A)
    assert replacement and replacement != "gone-for-good"
    assert _members(conn, replacement) == {A1, A2}


def test_a_team_already_bridged_is_left_alone(conn):
    _upgrade(conn)
    before = _team_group(conn, TEAM_A)
    conn.log.clear()
    _upgrade(conn)
    assert _team_group(conn, TEAM_A) == before


# ---------------------------------------------------------------------------
# Values duplicated from utils/team_groups.py
# ---------------------------------------------------------------------------


def test_the_name_matches_ensure_team_pii_group(conn):
    """The migration names groups exactly as `team_pii_group_name` does.

    The migration cannot call the async helper, so the name logic is duplicated.
    """
    from open_webui.utils import team_groups

    _upgrade(conn)
    name = conn.execute(
        text('SELECT name FROM "group" WHERE id = :g'), {"g": _team_group(conn, TEAM_A)}
    ).scalar()
    assert name == team_groups.team_pii_group_name("Alpha", TEAM_A)


def test_the_permissions_match_ensure_team_pii_group():
    from open_webui.utils import team_groups

    assert bridge.TEAM_PII_GROUP_PERMISSIONS == team_groups.TEAM_PII_GROUP_PERMISSIONS
    assert bridge.TEAM_ID_DISCRIMINATOR_LENGTH == team_groups.TEAM_ID_DISCRIMINATOR_LENGTH


def test_the_source_group_id_is_the_one_the_seed_migration_created():
    """`SOURCE_GROUP_ID` matches the `GROUP_ID` defined in seed migration 1782400007.

    The seed file is found by its revision id, not its filename, so renaming it
    does not break the test.
    """
    versions = pathlib.Path(bridge.__file__).parent
    seeds = [
        path
        for path in versions.glob("*.py")
        if 'revision: str = "1782400007"' in path.read_text(encoding="utf-8")
    ]
    assert len(seeds) == 1, seeds
    source = seeds[0].read_text(encoding="utf-8")
    assert f'GROUP_ID = "{SOURCE}"' in source, (
        f"the seed migration no longer defines GROUP_ID as {SOURCE!r}; "
        "this migration's narrow targeting is addressed at it"
    )


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------


def test_downgrade_puts_everyone_back(conn):
    before_groups = conn.execute(text('SELECT COUNT(*) FROM "group"')).scalar()

    _upgrade(conn)
    _downgrade(conn)

    assert _members(conn, SOURCE) == {A1, A2, B1, LONER}
    assert _team_group(conn, TEAM_A) is None
    assert _team_group(conn, TEAM_B) is None
    assert conn.execute(text('SELECT COUNT(*) FROM "group"')).scalar() == before_groups
    assert conn.execute(text("SELECT COUNT(*) FROM pii_policy_audit")).scalar() == 0


def test_downgrade_is_idempotent(conn):
    _upgrade(conn)
    _downgrade(conn)
    _downgrade(conn)
    assert _members(conn, SOURCE) == {A1, A2, B1, LONER}


def test_downgrade_refuses_a_group_somebody_joined_afterwards(conn):
    """Downgrade leaves a team group with members it did not add untouched, so an admin's policy is not revoked."""
    _upgrade(conn)
    team_a_group = _team_group(conn, TEAM_A)
    _add_member(conn, team_a_group, "u-newcomer")
    conn.commit()

    _downgrade(conn)

    assert _team_group(conn, TEAM_A) == team_a_group
    assert _members(conn, team_a_group) == {A1, A2, "u-newcomer"}
    # ...and the untouched team still comes back.
    assert _team_group(conn, TEAM_B) is None
    assert _members(conn, SOURCE) == {B1, LONER}


def test_downgrade_keeps_audit_rows_written_by_a_real_admin(conn):
    """Downgrade deletes only `system` audit rows carrying this revision id."""
    _upgrade(conn)
    conn.execute(
        text(
            "INSERT INTO pii_policy_audit VALUES "
            "('admin-row', 'member_added', :g, :u, 'admin-1', 'a@x.com', 'Because I said so', 99)"
        ),
        {"g": _team_group(conn, TEAM_B), "u": "u-someone"},
    )
    conn.commit()

    _downgrade(conn)

    remaining = conn.execute(text("SELECT id FROM pii_policy_audit")).fetchall()
    assert [r[0] for r in remaining] == ["admin-row"]


def test_downgrade_refuses_entirely_when_the_seeded_group_is_gone(conn):
    """Downgrade does nothing if the seeded group is gone.

    Deleting the team groups would leave their members unmasked.
    """
    _upgrade(conn)
    team_a_group = _team_group(conn, TEAM_A)
    conn.execute(text('DELETE FROM "group" WHERE id = :g'), {"g": SOURCE})
    conn.commit()

    _downgrade(conn)

    assert _team_group(conn, TEAM_A) == team_a_group
    assert _members(conn, team_a_group) == {A1, A2}


def test_downgrade_before_upgrade_does_nothing(conn):
    _downgrade(conn)
    assert _members(conn, SOURCE) == {A1, A2, B1, LONER}
    assert _team_group(conn, TEAM_A) is None
