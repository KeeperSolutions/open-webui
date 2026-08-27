"""Bridge existing teams to their own PII policy group

Every team that exists on the day this lands gets the group `teams.group_id`
points at, and every member of such a team **that the seeded instance-wide group
already masks** is moved into it — added to the team's group FIRST, then taken
out of the seeded one. Nobody's masking changes, in either direction: at every
instant between the two writes the person is in both groups, and a member the
seeded group does not mask is not touched at all.

⚠️ A MOVE, not an enrolment. See the loop in `upgrade` for why: enrolling every
member would newly enforce masking on people nobody decided to enforce, and it
would make a team that existed the day before behave differently from one
created the day after.

⚠️ **This is the one migration in this feature that changes existing data.**
Three properties are what make that acceptable:

  * **Nobody is unmasked, at any point.** Add-then-remove is not a preference; the
    reverse order opens a window in which the person is in neither group. The
    order is asserted by a test that reads the statement log, not by reading this
    comment.

  * **The whole prior state is read into Python before the first write.** Every
    decision is taken against that snapshot, so no decision can see this
    migration's own output.

    ⚠️ Measured, and worth stating precisely: with the source addressed by id
    (below), a variant that re-reads everything from the database mid-run
    produces the SAME result on every test in `test_bridge_migration.py`. The
    snapshot is not what fixes an observed bug here — the narrow criterion is.
    It is kept because it makes the whole class of order-dependence
    unreachable rather than merely absent, and the failure it forecloses is one
    that is invisible on a two-row database and unreproducible on a real one.

  * **It targets the seeded group by id, not "any group carrying the flag".**
    Today those are the same set. They stop being the same set on the first
    instance where an admin turns masking on for a group of their own — and there
    this migration would take people out of a group no team created and no team
    replaces. The narrow criterion cannot make that mistake, and it is what makes
    the point above safe as well: the groups this migration creates all carry the
    flag, so a flag-based criterion would start eating its own output.

People who are in the seeded group and in NO team stay exactly where they are.
The seeded group keeps its meaning — it is the policy for people a team does not
cover.

⚠️ The name and the permissions written here are DUPLICATED from
`utils/team_groups.py`. Alembic is synchronous and `ensure_team_pii_group` is
`async`, so the migration cannot call it. `test_bridge_migration.py` asserts the
two agree; that test is the only thing keeping the duplication honest.

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


# ⚠️ The seeded instance-wide policy group, addressed by the id migration
# 1782400007 gives it — a literal in an applied migration, which is history and
# therefore cannot change. NOT "whichever group carries the flag": see the module
# docstring. `test_bridge_migration.py` reads the seed migration's source and
# asserts this literal still matches it.
SOURCE_GROUP_ID = "pii-masking-policy"

# Same actor the seed migration used, for the same reason: the audit table's
# actor columns are NOT NULL and the thing acting here is the product.
SYSTEM_ACTOR_ID = "system"
SYSTEM_ACTOR_EMAIL = "system@open-webui"

# ⚠️ Must equal `utils.team_groups.TEAM_PII_GROUP_PERMISSIONS`. One key, because
# group permissions merge with OR and a fuller dict would silently make this
# group an opinion about everything else.
TEAM_PII_GROUP_PERMISSIONS = {"chat": {"pii_masking_enforced": True}}

# ⚠️ Must equal `utils.team_groups.TEAM_ID_DISCRIMINATOR_LENGTH`.
TEAM_ID_DISCRIMINATOR_LENGTH = 8

# The reason text names the CAUSE rather than the gesture. `member_removed`
# otherwise reads as protection being taken away, which is the one thing this
# migration never does — and the audit trail is read by people who were not here.
MOVE_REASON = (
    f"Moved to the team policy group by migration {revision}; "
    "masking is unchanged and now comes from the team."
)

# ⚠️ Written for every group this migration CREATES, and the spec's audit table
# does not list it. It is here because `downgrade` has no other way to see a team
# group with no members: the spec says downgrade recognises its own work "only
# through its own audit trail", and a group whose team is empty leaves no
# member_* rows at all. Same shape and same wording style as the row migration
# 1782400007 writes for the seeded group.
CREATE_REASON = (
    f"Created by migration {revision} so the team's masking policy has a destination."
)

# Rows are written with `executemany` in slices of this size rather than one
# statement per member, so an instance with hundreds of members does not issue
# hundreds of round trips.
BATCH = 500


def _team_pii_group_name(team_name: str, team_id: str) -> str:
    """⚠️ Must equal `utils.team_groups.team_pii_group_name`."""
    return f"PII — {team_name} · {team_id[:TEAM_ID_DISCRIMINATOR_LENGTH]}"


def _chunked(rows, size=BATCH):
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def _audit_row(event_type, group_id, user_id, reason, now):
    # Every raw insert goes through the model's own validator first. A raw
    # `INSERT` otherwise skips the invariants entirely, and this migration writes
    # two rows per member where the precedent wrote one — the exposure is an
    # order of magnitude larger, which is why the validator was extracted
    # rather than left inline in the model.
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
    """The entire prior state, as plain Python. Nothing here runs again later.

    ⚠️ Read in full before the first write, and never refreshed. See the module
    docstring for why a mid-run re-read is a correctness bug rather than a style
    preference.

    `permissions` is not consulted at all — the source group is addressed by id —
    which also removes the last place this migration would have had to reach into
    a JSON column, an operation whose syntax differs between SQLite and Postgres.
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

    # `uq_team_members_user_id` makes this a partition, not an overlap: one person
    # belongs to at most one team, so "the team's group" is never ambiguous.
    members_by_team = {}
    for team_id, user_id in bind.execute(
        sa.text("SELECT team_id, user_id FROM team_members ORDER BY team_id, user_id")
    ).fetchall():
        members_by_team.setdefault(team_id, []).append(user_id)

    # ⚠️ The audit table is deliberately NOT read here. Idempotency for the trail
    # rides on idempotency for the writes: a row is recorded exactly when a write
    # happens, and a second run performs no writes. An extra "have I already
    # logged this?" check looks like a third safeguard and is worse than nothing —
    # measured, see the note in `upgrade`.
    return teams, group_ids, memberships, members_by_team


def upgrade():
    bind = op.get_bind()
    now = int(time.time())

    teams, group_ids, memberships, members_by_team = _snapshot(bind)

    source_present = SOURCE_GROUP_ID in group_ids
    if not source_present:
        # The seed migration defers when an instance already had an enforcing
        # group of its own, so its group genuinely may not exist. Team groups are
        # still created — the bridge has to work for every team — but there is
        # nowhere to move anyone out of, and inventing one is not this migration's
        # decision to take.
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
        # ⚠️ Both halves are required. A non-null `group_id` alone is not proof:
        # `PRAGMA foreign_keys` is 0 on SQLite, so a deleted group leaves the
        # reference pointing at nothing, and trusting it would link the team to a
        # group that does not exist.
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
                    # Nobody made this; the product did. Same choice, and the same
                    # reasoning, as migration 1782400007 and `insert_new_group`.
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
            # ⚠️ ONLY people the seeded group already masks are moved. A team
            # member who is not in it is left exactly as they are.
            #
            # This is a MOVE, not an enrolment. Adding every member would newly
            # enforce masking on people nobody decided to enforce, and it would
            # make an existing team behave differently from a new one: a team
            # created after this lands gets an EMPTY policy group
            # (`routers/billing.py:create_team`) and joining it enrols nobody.
            # There is no reason a team that existed the day before should have
            # its members enrolled automatically, and every reason a policy
            # change should be somebody's decision.
            #
            # It also makes the module docstring's claim true rather than nearly
            # true: with this check, no one's masking changes in EITHER
            # direction.
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
            # ⚠️ One row per removal, never "one row per person, ever".
            #
            # An earlier draft suppressed this when a `system` row already
            # existed for the same (event_type, group_id, user_id). No test
            # could kill that check, which is how it was found — and the one
            # state where it fires is the state where it is WRONG: an admin
            # puts someone back into the seeded group after the migration ran,
            # the migration runs again, takes them out again, and the check
            # swallows the record of it. A membership write with no audit row is
            # the single thing this table exists to prevent.
            #
            # Idempotency does not need it: a second run finds the person
            # already out of the seeded group and writes nothing at all.
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

    # ⚠️ Every addition is issued before any removal. Not per member and then the
    # next member: in the window between these two blocks every person being moved
    # is in BOTH groups, which is the strongest form of "nobody is ever outside
    # both". `test_bridge_migration.py` asserts it from the statement log.
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
    """Undo exactly what this revision wrote, and nothing that resembles it.

    Its own work is identified through its own audit trail: rows whose actor is
    `system` and whose reason carries this revision id. An admin's rows are never
    matched and never deleted.

    ⚠️ What this cannot restore, said plainly:

      1. **Membership somebody changed afterwards.** If an admin took a person out
         of the team group, they are not put back into the seeded one — there is
         no way to tell that apart from someone who was never moved.
      2. **Timestamps and ordering.** Restored membership gets a new `created_at`.
      3. **Audit rows the application wrote in the meantime.** They stay, and they
         must.
      4. **Anything at all, if the seeded group is gone.** The destination no
         longer exists, so undoing the move would leave people masked by nothing.
         It refuses rather than doing half of it.
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
            # Somebody joined after the migration ran, or the group already had
            # members before it. Either way this group is no longer only this
            # migration's doing, so it is left completely alone — membership,
            # group and audit rows — and a later attempt can still act on it.
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
