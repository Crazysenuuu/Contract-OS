"""agreement numbering (spec 2.01 §46)

Revision ID: d1e2f3a4b5c6
Revises: b7d8e9f0a1c2
Create Date: 2026-09-23

Adds the server-generated business reference to ``agreements``:

    AGR-<org prefix>-<seq>       e.g. AGR-A1B2C3D4-00042

The UUID remains the primary identity; ``agreement_number`` is display and
search only (spec: "Don't depend on it as the primary database identity").
Unique per organization via a partial index — NULL is allowed for legacy
rows and the numbering sentinel, and multiple NULLs coexist.

Existing agreements are backfilled from each organization's row order so
references stay stable afterwards.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, Sequence[str], None] = "b7d8e9f0a1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agreements",
        sa.Column("agreement_number", sa.String(length=64), nullable=True),
    )

    # Backfill references for existing agreements, numbered per organization
    # by creation order. Row_number is reset per organization so the
    # sequence is org-scoped, matching next_agreement_number()'s MAX+1 logic.
    op.execute(
        """
        WITH numbered AS (
            SELECT
                id,
                'AGR-' || upper(substring(organization_id::text FROM 1 FOR 8))
                    || '-' || lpad(
                        (row_number() OVER (
                            PARTITION BY organization_id
                            ORDER BY created_at, id
                        ))::text,
                        5, '0'
                    ) AS agreement_number
            FROM agreements
        )
        UPDATE agreements a
        SET agreement_number = n.agreement_number
        FROM numbered n
        WHERE a.id = n.id
        """
    )

    op.create_index(
        "uq_agreements_org_number",
        "agreements",
        ["organization_id", "agreement_number"],
        unique=True,
        postgresql_where=sa.text("agreement_number IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_agreements_org_number", table_name="agreements")
    op.drop_column("agreements", "agreement_number")
