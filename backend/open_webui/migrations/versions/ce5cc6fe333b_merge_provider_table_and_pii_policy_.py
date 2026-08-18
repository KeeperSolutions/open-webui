"""merge provider table and pii policy heads

Revision ID: ce5cc6fe333b
Revises: 6db8c8c9e7e7, 1782400007
Create Date: 2026-08-18 00:00:00.000000

Joins upstream's provider-table chain (terminating at b0d23dcf13b7_merge_
legacy-pk_and_billing_scim_heads -> 6db8c8c9e7e7_add_provider_table_and_seed_
default_providers) with this fork's PII policy chain (terminating at
1782400007_seed_pii_policy_group), both of which branch from the same
81de4454640d_merge_scim_and_billing_heads ancestor. Same class of divergence
as 81de4454640d itself: two chains that grew from a shared merge point
without a common finishing merge.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ce5cc6fe333b'
down_revision: Union[str, tuple, None] = ('6db8c8c9e7e7', '1782400007')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
