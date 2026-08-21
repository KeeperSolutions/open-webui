"""G-B6 — the migration that moves existing teams onto their own policy group.

⚠️ This is the only gate in the bridge that changes data an environment already
has, so the tests are about the two things that could go wrong in a way nobody
would notice: somebody ending up outside every enforcing group, and a decision
being taken against a database this migration has already written to.

The pattern is the one from `routers/test_billing.py:161-190`: an in-memory
SQLite database with a hand-built minimal schema, the migration module's `op`
patched so `get_bind()` returns that connection, and `upgrade()` called directly.

The connection is wrapped in `Recorder` so the ORDER of the statements is a thing
tests can assert. "Added to the team group before being removed from the seeded
one" is not visible in the final state — both orders reach the same rows — and it
is the property the whole migration is arranged around.
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
    """Two teams, four people, and the shape the local database actually has.

    ⚠️ TWO teams, and one of them with TWO members, both on purpose. A single
    team hides the mutation where the source group is re-derived while the
    migration runs — the groups it creates carry the masking flag too, so the
    second team would be processed against a set that now includes the first
    team's group. A single member hides any per-member ordering bug, because the
    first write has nobody left to spoil the answer for.
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
# G-1 — one group per existing team
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
# G-2 — a member ends up in the team group and out of the seeded one
# ---------------------------------------------------------------------------


def test_team_members_move(conn):
    _upgrade(conn)
    assert _members(conn, _team_group(conn, TEAM_A)) == {A1, A2}
    assert _members(conn, _team_group(conn, TEAM_B)) == {B1}
    assert _members(conn, SOURCE) == {LONER}


def test_nobody_is_ever_outside_both_groups(conn):
    """⚠️ The property the migration is arranged around, read from the statement log.

    The final state cannot show it — add-then-remove and remove-then-add end in
    exactly the same rows. Only the order distinguishes them, and the difference
    is a window in which a person is masked by nothing.
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
    """⚠️ Every group this migration creates carries the masking flag too.

    So a source set derived from the FLAG rather than from the seeded id grows to
    include this migration's own output, and starts emptying groups it has no
    business touching. Addressing the source by id is what forecloses that, and
    this reads the property off the audit trail rather than off the final rows —
    a removal that left no record would pass the membership tests below.

    ⚠️ An earlier version of this test claimed the two-team fixture caught the
    flag-based variant by itself. It does not: one person belongs to at most one
    team (`uq_team_members_user_id`), so team B's members are never in team A's
    group and there is nothing to cross-contaminate. The variant is caught here
    and by `test_a_team_member_who_is_also_in_a_custom_group_keeps_that_membership`.
    """
    _add_member(conn, CUSTOM_GROUP, A1)
    conn.commit()

    _upgrade(conn)

    removed_from = {row[2] for row in _audit(conn, event_type="member_removed")}
    assert removed_from == {SOURCE}


# ---------------------------------------------------------------------------
# G-3 — O-5: somebody without a team is not touched
# ---------------------------------------------------------------------------


def test_a_person_with_no_team_stays_in_the_seeded_group(conn):
    _upgrade(conn)
    assert LONER in _members(conn, SOURCE)
    assert _audit(conn, user_id=LONER) == []


# ---------------------------------------------------------------------------
# G-11 — a custom enforcing group is not a source
# ---------------------------------------------------------------------------


def test_a_custom_enforcing_group_is_untouched(conn):
    """⚠️ The narrowing: the migration addresses the seeded group by id.

    A group an admin flagged themselves belongs to no team and is replaced by
    nothing, so emptying it would take masking away with nobody deciding it.
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
# G-5 — the audit trail
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
    """A bare `member_removed` reads as protection being withdrawn.

    It is the one thing this migration never does, and the trail is read by
    people who were not here to watch it run.
    """
    _upgrade(conn)
    reason = _audit(conn, event_type="member_removed", user_id=A1)[0][6]
    assert "unchanged" in reason and "team" in reason


def test_every_audit_row_goes_through_the_model_validator(conn):
    """⚠️ G-B5 exists for this. A raw INSERT skips every invariant otherwise.

    Counted, not merely patched: the assertion is that the validator saw as many
    calls as there are rows, so a shape that validates the first row and then
    writes the rest directly still fails.
    """
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
# G-4 — idempotency, on all three levels
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
    """⚠️ Found by a mutation that SURVIVED: the audit trail carried a third
    "have I already logged this?" check that no test could kill.

    The one state where such a check fires is the state where it is wrong. An
    admin puts someone back into the seeded group after the migration has run;
    the migration runs again and takes them out again. That is a second removal,
    and it needs a second row — a membership write with no record of it is the
    only thing this table exists to prevent.

    Idempotency is unaffected: `test_running_twice_changes_nothing` covers the
    ordinary second run, where nothing is written and so nothing is recorded.
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
# G-6 — an empty seeded group, and no seeded group at all
# ---------------------------------------------------------------------------


def test_an_empty_seeded_group_still_gets_every_team_a_group(conn):
    """⚠️ Not a hypothetical: this is what staging looks like.

    The seeded group there has no members, so the whole moving half of this
    migration has nobody to act on and only the creating half runs.
    """
    conn.execute(text("DELETE FROM group_member WHERE group_id = :g"), {"g": SOURCE})
    conn.commit()

    _upgrade(conn)

    assert _team_group(conn, TEAM_A) and _team_group(conn, TEAM_B)
    assert _members(conn, SOURCE) == set()
    assert _audit(conn, event_type="member_removed") == []
    assert _audit(conn, event_type="member_added") != []


def test_a_missing_seeded_group_is_not_an_error(conn):
    """The seed migration DEFERS when an instance already had an enforcing group.

    So an environment can legitimately have no group with that id at all. Teams
    still need their groups; there is simply nowhere to move anyone out of.
    """
    conn.execute(text("DELETE FROM group_member WHERE group_id = :g"), {"g": SOURCE})
    conn.execute(text('DELETE FROM "group" WHERE id = :g'), {"g": SOURCE})
    conn.commit()

    _upgrade(conn)

    assert _team_group(conn, TEAM_A) and _team_group(conn, TEAM_B)
    assert _members(conn, _team_group(conn, TEAM_A)) == {A1, A2}
    assert _audit(conn, event_type="member_removed") == []


def test_a_team_with_no_members_still_gets_a_group(conn):
    conn.execute(text("INSERT INTO teams VALUES ('team-empty', 'Gamma', NULL)"))
    conn.commit()
    _upgrade(conn)
    assert _team_group(conn, "team-empty")


# ---------------------------------------------------------------------------
# G-9 — a reference that points at nothing
# ---------------------------------------------------------------------------


def test_a_dangling_group_id_is_replaced(conn):
    """`PRAGMA foreign_keys` is 0, so deleting a group leaves the link behind."""
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
# G-10 — the duplication, kept honest
# ---------------------------------------------------------------------------


def test_the_name_matches_ensure_team_pii_group(conn):
    """⚠️ The migration cannot call `ensure_team_pii_group` — it is async.

    So the name is written twice. If the two ever disagree, a team created after
    the migration is named differently from a team migrated by it, and nothing
    else in the system would notice.
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
    """⚠️ The narrowing rests on this literal, so it is checked against its origin.

    Found by revision id rather than by filename: alembic identifies a migration
    by the `revision` inside it, so the file is free to be renamed and a test that
    pinned the path would fail for a reason that has nothing to do with the
    property.
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
# G-7, G-8 — downgrade
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
    """The precedent: a downgrade must not quietly revoke a policy an admin applied."""
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
    """Only `system` rows carrying this revision are its own to delete."""
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
    """⚠️ Nothing to return people to, so it does nothing rather than half of it.

    Deleting the team groups here would leave three people masked by no group at
    all — the exact failure the whole migration is ordered to avoid.
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
