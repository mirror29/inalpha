"""Link Evolver campaign state to its Paper Forward sandbox."""

from __future__ import annotations

from alembic import op

revision: str = "0051"
down_revision: str | None = "0050"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Persist the idempotent Paper sandbox handoff."""
    op.execute(
        """SET LOCAL lock_timeout = '10s';
ALTER TABLE evolution_campaigns ADD COLUMN forward_sandbox_id UUID UNIQUE
REFERENCES paper_evolution_forward_sandboxes(sandbox_id);"""
    )


def downgrade() -> None:
    """Detach Paper sandboxes from campaign projections."""
    op.execute("ALTER TABLE evolution_campaigns DROP COLUMN forward_sandbox_id")
