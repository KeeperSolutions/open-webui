"""Seed the PII masking policy group

TRAU-536. Creates one EMPTY group carrying `chat.pii_masking_enforced`, so an
admin can enforce masking for someone the moment this ships instead of first
having to work out that a group with that flag is the prerequisite.

⚠️ This narrows E-2 in PII-POLICY-ENGINE-SPEC.md, and does so deliberately. E-2
forbids the DASHBOARD creating a policy group as a side effect of an admin
action — a governance object appearing because someone clicked Enforce, with
nothing recording the decision. A migration is the opposite: the decision is
made once, in code, reviewed, and visible in the migration history of every
environment it runs in.

Three properties make it safe to run everywhere, production included:

  * The group is EMPTY. A group enforces nothing until someone is added to it,
    so this changes no user's masking state on the day it lands.
  * The permissions dict carries ONE key. Group permissions merge with OR, so a
    sparse dict adds the restriction and grants nothing; every other permission
    still falls back to the instance defaults. Copying a full dict here would
    silently make this group an opinion about twenty other things.
  * It is idempotent, and it defers. If ANY group already carries the flag —
    someone set one up by hand, or this already ran — nothing happens. Two
    policy groups would make the row action ask "which one?" forever after.

Revision ID: 1782400007
Revises: 1782400006
Create Date: 2026-08-14 00:00:00.000000

"""

import json
import time
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "1782400007"
down_revision: Union[str, None] = "1782400006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


GROUP_ID = "pii-masking-policy"
GROUP_NAME = "PII Masking Policy"
GROUP_DESCRIPTION = (
    "Members of this group must use PII masking. They cannot turn it off in "
    "settings or per conversation. Add or remove people from the PII Dashboard."
)

# Only the key this group exists for. See the note above on sparse permissions.
GROUP_PERMISSIONS = {"chat": {"pii_masking_enforced": True}}

# The audit table records who turned enforcement on. Here that is the product
# itself, and saying so is better than a group whose history opens with an
# enabled policy and no explanation. `actor_user_id`/`actor_email` are NOT NULL,
# so the system needs a name rather than a blank.
SYSTEM_ACTOR_ID = "system"
SYSTEM_ACTOR_EMAIL = "system@open-webui"


def _policy_group_exists(bind) -> bool:
    """True when any group already carries the flag.

    Read in Python rather than in SQL: `permissions` is a JSON column and the
    operators for reaching into it differ between SQLite and Postgres, both of
    which this migration has to run on.
    """
    rows = bind.execute(sa.text('SELECT permissions FROM "group"')).fetchall()
    for (permissions,) in rows:
        if not permissions:
            continue
        try:
            parsed = permissions if isinstance(permissions, dict) else json.loads(permissions)
        except (TypeError, ValueError):
            continue
        if (parsed.get("chat") or {}).get("pii_masking_enforced") is True:
            return True
    return False


def upgrade():
    bind = op.get_bind()

    if _policy_group_exists(bind):
        # An environment that already has one is already set up. Leave it alone.
        return

    now = int(time.time())

    # ⚠️ Clear any membership rows left over for this id before creating it.
    #
    # `group_member.group_id` is declared `ForeignKey('group.id', ondelete=
    # 'CASCADE')`, so deleting a group is SUPPOSED to take its members with it —
    # and on Postgres it does. On SQLite it does not: `PRAGMA foreign_keys`
    # defaults to OFF per connection and this application never turns it on, so
    # a deleted group leaves its membership rows behind.
    #
    # That matters here and almost nowhere else, because this group's id is
    # fixed rather than a uuid. A delete-then-recreate cycle on SQLite would
    # otherwise hand the new group the old group's members — putting people back
    # under mandatory masking with nobody deciding it and no audit row to
    # explain it.
    #
    # A no-op on Postgres, where the orphans cannot exist. Kept anyway: SQLite is
    # what development runs on.
    #
    # Scoped to this one id. Other groups' membership rows are not reachable by
    # this statement, and no other group can hold this id — every group is
    # created through `Groups.insert_new_group`, which assigns `uuid4()`.
    bind.execute(sa.text("DELETE FROM group_member WHERE group_id = :id"), {"id": GROUP_ID})

    bind.execute(
        sa.text(
            'INSERT INTO "group" (id, user_id, name, description, data, meta, permissions, created_at, updated_at) '
            "VALUES (:id, :user_id, :name, :description, :data, :meta, :permissions, :created_at, :updated_at)"
        ),
        {
            "id": GROUP_ID,
            # No creator: nobody made this, the migration did. `user_id` is only
            # ever read for display, so an empty string is honest where naming
            # some admin who did not do it would not be.
            "user_id": "",
            "name": GROUP_NAME,
            "description": GROUP_DESCRIPTION,
            "data": json.dumps({}),
            "meta": None,
            "permissions": json.dumps(GROUP_PERMISSIONS),
            "created_at": now,
            "updated_at": now,
        },
    )

    bind.execute(
        sa.text(
            "INSERT INTO pii_policy_audit "
            "(id, event_type, group_id, user_id, actor_user_id, actor_email, reason, event_ts) "
            "VALUES (:id, 'policy_enabled', :group_id, NULL, :actor_user_id, :actor_email, :reason, :event_ts)"
        ),
        {
            "id": str(uuid.uuid4()),
            "group_id": GROUP_ID,
            "actor_user_id": SYSTEM_ACTOR_ID,
            "actor_email": SYSTEM_ACTOR_EMAIL,
            "reason": "Created by migration 1782400007 so the policy has a destination.",
            "event_ts": now,
        },
    )


def downgrade():
    bind = op.get_bind()

    # Only ever removes the group this migration created, and only while it is
    # still empty. Once someone has been added, deleting it would revoke a
    # policy an admin applied — a downgrade must not do that quietly.
    members = bind.execute(
        sa.text("SELECT COUNT(*) FROM group_member WHERE group_id = :id"),
        {"id": GROUP_ID},
    ).scalar()

    if members:
        return

    bind.execute(sa.text("DELETE FROM pii_policy_audit WHERE group_id = :id"), {"id": GROUP_ID})
    bind.execute(sa.text('DELETE FROM "group" WHERE id = :id'), {"id": GROUP_ID})
