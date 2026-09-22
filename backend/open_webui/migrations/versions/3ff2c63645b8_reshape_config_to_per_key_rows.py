"""reshape config to per key rows

Revision ID: 3ff2c63645b8
Revises: 461111b60977
Create Date: 2026-06-17 00:50:51.477073

NOTE (fork — TRAU-534 v0.9.6→v0.11.0 upgrade): upstream v0.11.0 introduced
this migration to replace the single-row JSON-blob ``config`` table with a
per-key ``config`` table (key TEXT PK, value JSON), backing its new
``open_webui.models.config.Config`` async store.

**This fork deliberately does NOT adopt that store (Risk #1 in the upgrade
runbook).** It keeps the JSON-blob ``config`` table and the
``ConfigVar`` / ``AppConfig`` backbone in ``open_webui/config.py``. Running
the original upgrade here would rename ``config`` → ``config_old`` and
build a per-key table the fork has no code to read, breaking all config
persistence.

The migration is kept as a **no-op** rather than deleted so the Alembic
revision chain (``461111b60977`` → ``3ff2c63645b8`` → ``4c5ce3d2f27f``)
stays intact for any DB that has already stamped this revision. See
``md-docs/upgrade-0.9.6-to-0.11.0.md``.
"""

from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = '3ff2c63645b8'
down_revision: Union[str, None] = '461111b60977'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Intentionally a no-op — see module docstring (fork keeps the JSON-blob config table).
    pass


def downgrade() -> None:
    # Intentionally a no-op — see module docstring.
    pass
