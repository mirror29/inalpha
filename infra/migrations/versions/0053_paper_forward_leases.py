"""Add durable fencing leases for Paper Forward observation workers."""

from __future__ import annotations

from alembic import op

revision: str = "0053"
down_revision: str | None = "0052"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Fence observation writes so restarts and duplicate workers cannot double-apply evidence."""
    op.execute(
        """SET LOCAL lock_timeout = '10s';
ALTER TABLE paper_evolution_forward_sandboxes
  ADD COLUMN lease_owner TEXT,
  ADD COLUMN lease_token UUID,
  ADD COLUMN lease_expires_at TIMESTAMPTZ,
  ADD CONSTRAINT ck_paper_forward_lease_tuple CHECK (
    (lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL) OR
    (lease_owner IS NOT NULL AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)
  );"""
    )


def downgrade() -> None:
    """Remove leases without changing persisted observations or terminal evidence."""
    op.execute(
        """SET LOCAL lock_timeout = '10s';
ALTER TABLE paper_evolution_forward_sandboxes
  DROP CONSTRAINT ck_paper_forward_lease_tuple,
  DROP COLUMN lease_expires_at,
  DROP COLUMN lease_token,
  DROP COLUMN lease_owner;"""
    )
