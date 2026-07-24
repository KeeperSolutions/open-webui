"""Add provider table and seed default providers

Revision ID: 6db8c8c9e7e7
Revises: b0d23dcf13b7
Create Date: 2026-07-24 00:00:00.000000

Converts the fork's own 019_add_providers_table.py (Peewee) into an Alembic
revision. Necessary because upstream deleted its entire Peewee migration
layer in the v0.8.12 -> v0.9.6 range (internal/db.py's Peewee runner is
gone) — this table is the one remaining reason this fork ever needed
Peewee at all. The numbered 001-019 Peewee migration file is left in place
in internal/migrations/ (dead, no longer executed by anything) rather than
deleted, matching how other retired code in this upgrade was handled;
delete it in a follow-up once this revision has run everywhere.

model_id_patterns/model_patterns are stored as sa.Text() (JSON-encoded via
JSONField, see internal/db.py) rather than a native JSON column, matching
this table's own ORM model (models/providers.py) and the project-wide
convention of TEXT-backed JSON for cross-dialect (SQLite/Postgres)
portability — see JSONField's own docstring.
"""

import json
import time
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '6db8c8c9e7e7'
down_revision: Union[str, None] = 'b0d23dcf13b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

provider_t = sa.table(
    'provider',
    sa.column('id', sa.Text),
    sa.column('name', sa.Text),
    sa.column('logo_light_url', sa.Text),
    sa.column('logo_dark_url', sa.Text),
    sa.column('logo_url', sa.Text),
    sa.column('model_id_patterns', sa.Text),
    sa.column('model_patterns', sa.Text),
    sa.column('priority', sa.Integer),
    sa.column('is_active', sa.Boolean),
    sa.column('created_at', sa.BigInteger),
    sa.column('updated_at', sa.BigInteger),
)


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if 'provider' in inspector.get_table_names():
        return  # Already created (e.g. Peewee ran it before this revision existed) — skip everything

    op.create_table(
        'provider',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('logo_light_url', sa.Text(), nullable=True),
        sa.Column('logo_dark_url', sa.Text(), nullable=True),
        sa.Column('logo_url', sa.Text(), nullable=True),
        sa.Column('model_id_patterns', sa.Text(), nullable=False),
        sa.Column('model_patterns', sa.Text(), nullable=True),
        sa.Column('priority', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('updated_at', sa.BigInteger(), nullable=False),
    )

    now = int(time.time())

    providers = [
        {
            'id': 'openai',
            'name': 'OpenAI',
            'logo_light_url': '/providers/openai-light.svg',
            'logo_dark_url': '/providers/openai-dark.svg',
            'logo_url': '/providers/openai-light.svg',
            'model_id_patterns': json.dumps(["^gpt-", "^o1-", "^text-davinci", "^text-curie", "^text-babbage"]),
            'model_patterns': None,
            'priority': 100,
            'is_active': True,
            'created_at': now,
            'updated_at': now,
        },
        {
            'id': 'anthropic',
            'name': 'Anthropic',
            'logo_light_url': '/providers/anthropic-light.svg',
            'logo_dark_url': '/providers/anthropic-dark.svg',
            'logo_url': '/providers/anthropic-light.svg',
            'model_id_patterns': json.dumps(["^claude-"]),
            'model_patterns': json.dumps([{
                'name': 'claude',
                'patterns': ['^claude-3', '^claude-instant'],
                'logo_url': '/providers/models/claude-light.svg',
                'logo_light_url': '/providers/models/claude-light.svg',
                'logo_dark_url': '/providers/models/claude-dark.svg',
            }]),
            'priority': 100,
            'is_active': True,
            'created_at': now,
            'updated_at': now,
        },
        {
            'id': 'google',
            'name': 'Google',
            'logo_light_url': '/providers/google-light.svg',
            'logo_dark_url': '/providers/google-dark.svg',
            'logo_url': '/providers/google-light.svg',
            'model_id_patterns': json.dumps(["^gemini-", "^gemini:", "^palm-", "^gemma:", "^gemma[0-9]"]),
            'model_patterns': json.dumps([{
                'name': 'gemini',
                'patterns': ['^gemini-', '^gemini:'],
                'logo_url': '/providers/models/gemini-light.svg',
                'logo_light_url': '/providers/models/gemini-light.svg',
                'logo_dark_url': '/providers/models/gemini-dark.svg',
            }]),
            'priority': 100,
            'is_active': True,
            'created_at': now,
            'updated_at': now,
        },
        {
            'id': 'meta',
            'name': 'Meta',
            'logo_light_url': '/providers/meta-light.svg',
            'logo_dark_url': '/providers/meta-dark.svg',
            'logo_url': '/providers/meta-light.svg',
            'model_id_patterns': json.dumps(["^llama[0-9]", "^llama-", "^llama2", "^llama3", "^codellama"]),
            'model_patterns': None,
            'priority': 90,
            'is_active': True,
            'created_at': now,
            'updated_at': now,
        },
        {
            'id': 'ollama',
            'name': 'Ollama',
            'logo_light_url': '/providers/ollama-light.svg',
            'logo_dark_url': '/providers/ollama-dark.svg',
            'logo_url': '/providers/ollama-light.svg',
            'model_id_patterns': json.dumps([]),
            'model_patterns': None,
            'priority': 10,
            'is_active': True,
            'created_at': now,
            'updated_at': now,
        },
    ]

    conn.execute(provider_t.insert(), providers)


def downgrade() -> None:
    op.drop_table('provider')
