"""Attach authoritative Data asset identifiers to facts and snapshots."""

from __future__ import annotations

from alembic import op

revision: str = "0049"
down_revision: str | None = "0048"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Backfill stable IDs while retaining legacy event codes for compatibility."""
    op.execute(
        """SET LOCAL lock_timeout = '10s';
ALTER TABLE market_event_facts ADD COLUMN asset_ids TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[];
ALTER TABLE market_event_facts DISABLE TRIGGER market_event_facts_immutable;
UPDATE market_event_facts SET asset_ids=ARRAY(
  SELECT DISTINCT 'asset:'||UPPER(TRIM(asset)) FROM unnest(assets) AS asset
  WHERE TRIM(asset)<>'' ORDER BY 1
);
ALTER TABLE market_event_facts ENABLE TRIGGER market_event_facts_immutable;
CREATE INDEX ix_market_event_facts_asset_ids
ON market_event_facts USING GIN(asset_ids);
ALTER TABLE market_event_snapshots ADD COLUMN asset_ids TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[];
ALTER TABLE market_event_snapshots DISABLE TRIGGER market_event_snapshots_immutable;
UPDATE market_event_snapshots SET asset_ids=ARRAY(
  SELECT DISTINCT 'asset:'||UPPER(TRIM(asset)) FROM unnest(assets) AS asset
  WHERE TRIM(asset)<>'' ORDER BY 1
);
ALTER TABLE market_event_snapshots ENABLE TRIGGER market_event_snapshots_immutable;"""
    )


def downgrade() -> None:
    """Remove derived asset identifiers while preserving legacy codes."""
    op.execute(
        """SET LOCAL lock_timeout = '10s';
ALTER TABLE market_event_snapshots DROP COLUMN asset_ids;
DROP INDEX ix_market_event_facts_asset_ids;
ALTER TABLE market_event_facts DROP COLUMN asset_ids;"""
    )
