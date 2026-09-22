"""Bridge existing teams to their own PII policy group

Every team gets the group `teams.group_id` points at. Each team member who is
already in the seeded instance-wide policy group is moved into the team's group:
added to the team group first, then removed from the seeded one. Nobody's
masking changes. Team members outside the seeded group are not touched, so no
one gets masking nobody decided on. People in the seeded group but in no team
stay there.

Guarantees:

  * Nobody is unmasked at any point. All additions are written before any
    removal; `test_bridge_migration.py` checks the order in the statement log.
  * All decisions are made against a snapshot read before the first write, so
    none of them depends on this migration's own output.
  * The seeded group is addressed by id, not by "has the masking flag". Admins
    can enable masking on their own groups, and the team groups created here
    carry the flag too; neither must be emptied.

The group name and permissions duplicate `utils/team_groups.py`, because Alembic
is synchronous and `ensure_team_pii_group` is async. `test_bridge_migration.py`
asserts the two agree.

Revision ID: b6d1a4f0c7e2
Revises: a7c3f1b9e204
Create Date: 2026-08-21 15:10:00.000000

"""

import json
import logging
import time
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b6d1a4f0c7e2"
down_revision: Union[str, None] = "a7c3f1b9e204"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

log = logging.getLogger("alembic.runtime.migration")


# Id of the seeded instance-wide policy group, as created by migration
# 1782400007. `test_bridge_migration.py` asserts it matches the seed migration.
SOURCE_GROUP_ID = "pii-masking-policy"

# Same system actor as the seed migration; the audit actor columns are NOT NULL.
SYSTEM_ACTOR_ID = "system"
SYSTEM_ACTOR_EMAIL = "system@open-webui"

# Must equal `utils.team_groups.TEAM_PII_GROUP_PERMISSIONS`. Group permissions
# merge with OR, so any extra key would grant that permission to every member.
TEAM_PII_GROUP_PERMISSIONS = {"chat": {"pii_masking_enforced": True}}

# Must equal `utils.team_groups.TEAM_ID_DISCRIMINATOR_LENGTH`.
TEAM_ID_DISCRIMINATOR_LENGTH = 8

# States the cause, so the `member_removed` rows do not read as masking being
# taken away.
MOVE_REASON = (
    f"Moved to the team policy group by migration {revision}; "
    "masking is unchanged and now comes from the team."
)

# Recorded for every group this migration creates. `downgrade` finds its own
# work only through the audit trail, and a group for an empty team has no
# member_* rows. Matches the row migration 1782400007 writes for the seeded group.
CREATE_REASON = (
    f"Created by migration {revision} so the team's masking policy has a destination."
)

# Rows per `executemany` call, so large instances do not issue one statement
# per member.
BATCH = 500


def _team_pii_group_name(team_name: str, team_id: str) -> str:
    """Must equal `utils.team_groups.team_pii_group_name`."""
    return f"PII — {team_name} · {team_id[:TEAM_ID_DISCRIMINATOR_LENGTH]}"


def _chunked(rows, size=BATCH):
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def _audit_row(event_type, group_id, user_id, reason, now):
    # Raw inserts skip the model, so run the model's validator on each row.
    from open_webui.models.pii_policy_audit import validate_pii_policy_event

    validate_pii_policy_event(
        event_type=event_type,
        group_id=group_id,
        actor_user_id=SYSTEM_ACTOR_ID,
        actor_email=SYSTEM_ACTOR_EMAIL,
        user_id=user_id,
        reason=reason,
    )
    return {
        "id": str(uuid.uuid4()),
        "event_type": event_type,
        "group_id": group_id,
        "user_id": user_id,
        "actor_user_id": SYSTEM_ACTOR_ID,
        "actor_email": SYSTEM_ACTOR_EMAIL,
        "reason": reason,
        "event_ts": now,
    }


def _insert_audit(bind, rows):
    if not rows:
        return
    statement = sa.text(
        "INSERT INTO pii_policy_audit "
        "(id, event_type, group_id, user_id, actor_user_id, actor_email, reason, event_ts) "
        "VALUES (:id, :event_type, :group_id, :user_id, :actor_user_id, :actor_email, "
        ":reason, :event_ts)"
    )
    for batch in _chunked(rows):
        bind.execute(statement, batch)


def _snapshot(bind):
    """Read the whole prior state into plain Python before any write.

    It is never refreshed, so no decision depends on this migration's own writes.
    `permissions` is not read, which avoids JSON queries whose syntax differs
    between SQLite and Postgres.
    """
    teams = [
        (team_id, name, group_id)
        for team_id, name, group_id in bind.execute(
            sa.text("SELECT id, name, group_id FROM teams ORDER BY id")
        ).fetchall()
    ]

    group_ids = {
        gid for (gid,) in bind.execute(sa.text('SELECT id FROM "group"')).fetchall()
    }

    memberships = {
        (gid, uid)
        for gid, uid in bind.execute(
            sa.text("SELECT group_id, user_id FROM group_member")
        ).fetchall()
    }

    # `uq_team_members_user_id` means each person is in at most one team.
    members_by_team = {}
    for team_id, user_id in bind.execute(
        sa.text("SELECT team_id, user_id FROM team_members ORDER BY team_id, user_id")
    ).fetchall():
        members_by_team.setdefault(team_id, []).append(user_id)

    # The audit table is not read. A row is recorded exactly when a write
    # happens, and a second run writes nothing, so the trail is idempotent too.
    # See the note on `member_removed` in `upgrade`.
    return teams, group_ids, memberships, members_by_team


def upgrade():
    bind = op.get_bind()
    now = int(time.time())

    teams, group_ids, memberships, members_by_team = _snapshot(bind)

    source_present = SOURCE_GROUP_ID in group_ids
    if not source_present:
        # The seed migration skips instances that already had an enforcing
        # group, so the seeded group may not exist. Team groups are still
        # created, but nobody is moved.
        log.info(
            "bridge_team_pii_groups: %s is not present; creating team groups only",
            SOURCE_GROUP_ID,
        )

    new_groups = []      # rows for "group"
    links = []           # teams.group_id updates
    additions = []       # rows for group_member
    removals = []        # user ids to take out of the source group
    audit_rows = []

    for team_id, team_name, existing_group_id in teams:
        # SQLite runs with `PRAGMA foreign_keys` off, so a non-null `group_id`
        # can point at a deleted group. Only reuse it if the group exists.
        if existing_group_id and existing_group_id in group_ids:
            team_group_id = existing_group_id
        else:
            if existing_group_id:
                log.warning(
                    "bridge_team_pii_groups: team %s pointed at missing group %s; "
                    "creating a replacement",
                    team_id,
                    existing_group_id,
                )
            team_group_id = str(uuid.uuid4())
            new_groups.append(
                {
                    "id": team_group_id,
                    # No user created this group; same as migration 1782400007.
                    "user_id": "",
                    "name": _team_pii_group_name(team_name, team_id),
                    "description": "",
                    "data": json.dumps({}),
                    "meta": None,
                    "permissions": json.dumps(TEAM_PII_GROUP_PERMISSIONS),
                    "created_at": now,
                    "updated_at": now,
                }
            )
            links.append({"team_id": team_id, "group_id": team_group_id})
            audit_rows.append(
                _audit_row("policy_enabled", team_group_id, None, CREATE_REASON, now)
            )

        for user_id in members_by_team.get(team_id, []):
            # Only move people the seeded group already masks. Enrolling every
            # member would enforce masking nobody decided on, and would treat
            # existing teams differently from new ones, whose policy group
            # starts empty (`create_team` in `routers/billing.py`).
            if not (source_present and (SOURCE_GROUP_ID, user_id) in memberships):
                continue

            if (team_group_id, user_id) not in memberships:
                additions.append(
                    {
                        "id": str(uuid.uuid4()),
                        "group_id": team_group_id,
                        "user_id": user_id,
                        "created_at": now,
                        "updated_at": now,
                    }
                )
                audit_rows.append(
                    _audit_row("member_added", team_group_id, user_id, MOVE_REASON, now)
                )

            removals.append(user_id)
            # One audit row per removal, even if an earlier run already logged
            # one for this person. If an admin put them back and the migration
            # runs again, the second removal must be recorded too. A rerun with
            # nothing to do reaches no removal, so this stays idempotent.
            audit_rows.append(
                _audit_row("member_removed", SOURCE_GROUP_ID, user_id, MOVE_REASON, now)
            )

    # --- writes start here; nothing above this line is re-read -----------------

    if new_groups:
        group_insert = sa.text(
            'INSERT INTO "group" '
            "(id, user_id, name, description, data, meta, permissions, created_at, updated_at) "
            "VALUES (:id, :user_id, :name, :description, :data, :meta, :permissions, "
            ":created_at, :updated_at)"
        )
        for batch in _chunked(new_groups):
            bind.execute(group_insert, batch)

        link_update = sa.text("UPDATE teams SET group_id = :group_id WHERE id = :team_id")
        for batch in _chunked(links):
            bind.execute(link_update, batch)

    # All additions are issued before any removal, so between these two blocks
    # everyone being moved is in both groups. `test_bridge_migration.py` checks
    # the order from the statement log.
    if additions:
        member_insert = sa.text(
            "INSERT INTO group_member (id, group_id, user_id, created_at, updated_at) "
            "VALUES (:id, :group_id, :user_id, :created_at, :updated_at)"
        )
        for batch in _chunked(additions):
            bind.execute(member_insert, batch)

    if removals:
        member_delete = sa.text(
            "DELETE FROM group_member WHERE group_id = :group_id AND user_id = :user_id"
        )
        for batch in _chunked(
            [{"group_id": SOURCE_GROUP_ID, "user_id": uid} for uid in removals]
        ):
            bind.execute(member_delete, batch)

    _insert_audit(bind, audit_rows)


def downgrade():
    """Undo exactly what this revision wrote.

    Its work is found through audit rows whose actor is `system` and whose reason
    contains this revision id; admin rows are never matched.

    Limits:

      1. A person an admin removed from the team group afterwards is not put back
         into the seeded group; that case cannot be told apart from someone never
         moved.
      2. Restored memberships get a new `created_at`.
      3. Audit rows the application wrote since the upgrade are kept.
      4. If the seeded group no longer exists, nothing is undone, because
         undoing the move would leave people unmasked.
    """
    bind = op.get_bind()
    marker = f"%{revision}%"

    rows = bind.execute(
        sa.text(
            "SELECT event_type, group_id, user_id FROM pii_policy_audit "
            "WHERE actor_user_id = :actor AND reason LIKE :marker"
        ),
        {"actor": SYSTEM_ACTOR_ID, "marker": marker},
    ).fetchall()

    if not rows:
        return

    created_groups = {gid for event_type, gid, _ in rows if event_type == "policy_enabled"}
    added_by_group = {}
    removed_users = set()
    for event_type, gid, user_id in rows:
        if event_type == "member_added":
            added_by_group.setdefault(gid, set()).add(user_id)
        elif event_type == "member_removed" and gid == SOURCE_GROUP_ID:
            removed_users.add(user_id)

    source_exists = (
        bind.execute(
            sa.text('SELECT 1 FROM "group" WHERE id = :id'), {"id": SOURCE_GROUP_ID}
        ).scalar()
        is not None
    )
    if removed_users and not source_exists:
        log.warning(
            "bridge_team_pii_groups: %s no longer exists; refusing to undo the move",
            SOURCE_GROUP_ID,
        )
        return

    now = int(time.time())

    for group_id in sorted(created_groups | set(added_by_group)):
        current = {
            uid
            for (uid,) in bind.execute(
                sa.text("SELECT user_id FROM group_member WHERE group_id = :g"),
                {"g": group_id},
            ).fetchall()
        }
        mine = added_by_group.get(group_id, set())
        if current - mine:
            # The group has members this migration did not add. Leave its
            # membership, the group and its audit rows untouched, so a later
            # downgrade can still act on it.
            log.warning(
                "bridge_team_pii_groups: group %s has members this revision did not add; "
                "leaving it untouched",
                group_id,
            )
            continue

        # A second downgrade must not double the row: the unique constraint on
        # (group_id, user_id) would raise rather than skip.
        already_back = {
            uid
            for (uid,) in bind.execute(
                sa.text("SELECT user_id FROM group_member WHERE group_id = :g"),
                {"g": SOURCE_GROUP_ID},
            ).fetchall()
        }
        restore = sorted((mine & removed_users) - already_back)
        if restore:
            bind.execute(
                sa.text(
                    "INSERT INTO group_member (id, group_id, user_id, created_at, updated_at) "
                    "VALUES (:id, :group_id, :user_id, :created_at, :updated_at)"
                ),
                [
                    {
                        "id": str(uuid.uuid4()),
                        "group_id": SOURCE_GROUP_ID,
                        "user_id": uid,
                        "created_at": now,
                        "updated_at": now,
                    }
                    for uid in restore
                ],
            )

        bind.execute(
            sa.text("DELETE FROM group_member WHERE group_id = :g"), {"g": group_id}
        )

        if group_id in created_groups:
            bind.execute(
                sa.text("UPDATE teams SET group_id = NULL WHERE group_id = :g"),
                {"g": group_id},
            )
            bind.execute(sa.text('DELETE FROM "group" WHERE id = :g'), {"g": group_id})

        # Only this revision's system rows: the team group's own, and the
        # member_removed rows it wrote against the seeded group for these people.
        bind.execute(
            sa.text(
                "DELETE FROM pii_policy_audit "
                "WHERE actor_user_id = :actor AND reason LIKE :marker AND group_id = :g"
            ),
            {"actor": SYSTEM_ACTOR_ID, "marker": marker, "g": group_id},
        )
        if mine:
            bind.execute(
                sa.text(
                    "DELETE FROM pii_policy_audit "
                    "WHERE actor_user_id = :actor AND reason LIKE :marker "
                    "AND group_id = :source AND user_id = :u"
                ),
                [
                    {
                        "actor": SYSTEM_ACTOR_ID,
                        "marker": marker,
                        "source": SOURCE_GROUP_ID,
                        "u": uid,
                    }
                    for uid in sorted(mine)
                ],
            )
