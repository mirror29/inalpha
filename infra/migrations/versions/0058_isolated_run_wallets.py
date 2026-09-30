"""Isolate live paper wallets and preserve legacy accounting evidence."""

from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add owner-bound run books without moving legacy balances or positions."""
    op.execute("""
    ALTER TABLE strategy_runs ADD COLUMN accounting_status TEXT NOT NULL DEFAULT 'legacy_unverified'
      CHECK (accounting_status IN ('verified','legacy_unverified','contaminated'));
    ALTER TABLE strategy_runs ADD COLUMN accounting_note TEXT;
    ALTER TABLE strategy_runs ADD COLUMN original_cumulative_pnl NUMERIC;
    ALTER TABLE strategy_runs ADD CONSTRAINT strategy_runs_owner_key UNIQUE (id, account_id);
    UPDATE strategy_runs SET original_cumulative_pnl=cumulative_pnl,
      status=CASE WHEN status='running' THEN 'stopped' ELSE status END,
      stopped_at=CASE WHEN status='running' THEN NOW() ELSE stopped_at END,
      accounting_note='Legacy shared book paused; original trades and balances preserved';
    UPDATE strategy_runs r SET accounting_status='contaminated',
      accounting_note='Evidence: position or closed trade opened before this run'
    WHERE EXISTS (SELECT 1 FROM strategy_run_decisions d JOIN closed_trades c
        ON c.close_order_id=d.order_id AND c.account_id=r.account_id
        JOIN orders opening ON opening.client_order_id=c.open_order_id AND opening.account_id=c.account_id
        WHERE d.run_id=r.id AND c.open_ts<r.started_at);
    CREATE TABLE paper_legacy_attributions (
      closed_trade_id BIGINT PRIMARY KEY REFERENCES closed_trades(id),
      run_id UUID NOT NULL REFERENCES strategy_runs(id),
      evidence JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE TABLE strategy_run_wallets (
      run_id UUID PRIMARY KEY, account_id UUID NOT NULL,
      base_currency TEXT NOT NULL, initial_cash NUMERIC NOT NULL CHECK (initial_cash>0),
      cash_balances JSONB NOT NULL, revision BIGINT NOT NULL DEFAULT 0, quote_currency TEXT NOT NULL, initial_quote_cash NUMERIC NOT NULL,
      funding_through TIMESTAMPTZ NOT NULL DEFAULT NOW(), funding_warning TEXT, released_at TIMESTAMPTZ,
      final_equity NUMERIC, last_equity NUMERIC, valuation_at TIMESTAMPTZ,
      valuation_warnings JSONB NOT NULL DEFAULT '[]', created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      UNIQUE (run_id, account_id),
      FOREIGN KEY (run_id,account_id) REFERENCES strategy_runs(id,account_id)
    );
    DROP INDEX strategy_runs_one_running_per_candidate;
    CREATE UNIQUE INDEX strategy_runs_one_running_per_candidate
      ON strategy_runs(account_id,candidate_id) WHERE status='running';
    CREATE INDEX run_wallets_owner_idx ON strategy_run_wallets(account_id);
    ALTER TABLE positions DROP CONSTRAINT positions_pkey;
    ALTER TABLE positions ADD COLUMN run_id UUID;
    CREATE UNIQUE INDEX positions_main_key ON positions(account_id,venue,symbol) WHERE run_id IS NULL;
    CREATE UNIQUE INDEX positions_run_key ON positions(account_id,run_id,venue,symbol) WHERE run_id IS NOT NULL;
    ALTER TABLE positions ADD FOREIGN KEY (run_id,account_id) REFERENCES strategy_run_wallets(run_id,account_id);
    ALTER TABLE orders ADD COLUMN run_id UUID;
    ALTER TABLE orders ADD FOREIGN KEY (run_id,account_id) REFERENCES strategy_run_wallets(run_id,account_id);
    ALTER TABLE closed_trades ADD COLUMN run_id UUID;
    ALTER TABLE closed_trades ADD FOREIGN KEY (run_id,account_id) REFERENCES strategy_run_wallets(run_id,account_id);
    CREATE INDEX orders_run_idx ON orders(run_id,ts_event) WHERE run_id IS NOT NULL;
    CREATE INDEX closed_trades_run_idx ON closed_trades(run_id) WHERE run_id IS NOT NULL;
    CREATE TABLE paper_cash_events (
      id BIGSERIAL PRIMARY KEY, account_id UUID NOT NULL REFERENCES accounts(account_id),
      run_id UUID, event_key TEXT NOT NULL UNIQUE,
      kind TEXT NOT NULL CHECK (kind IN ('capital_in','capital_out','fill','funding')),
      currency TEXT NOT NULL, amount NUMERIC NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      FOREIGN KEY (run_id,account_id) REFERENCES strategy_run_wallets(run_id,account_id)
    );
    CREATE INDEX paper_cash_events_run_idx ON paper_cash_events(run_id,kind);
    """)


def downgrade() -> None:
    """Reject unsafe rollback once isolated books exist."""
    op.execute("""
    DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM strategy_run_wallets) THEN
        RAISE EXCEPTION 'Isolated wallets exist; keep schema and pause runs for rollback';
      END IF;
    END $$;
    DROP TABLE paper_legacy_attributions;
    DROP TABLE paper_cash_events;
    ALTER TABLE closed_trades DROP COLUMN run_id;
    ALTER TABLE orders DROP COLUMN run_id;
    ALTER TABLE positions DROP COLUMN run_id;
    DROP INDEX IF EXISTS positions_main_key;
    ALTER TABLE positions ADD PRIMARY KEY (account_id,venue,symbol);
    DROP TABLE strategy_run_wallets;
    DROP INDEX strategy_runs_one_running_per_candidate;
    CREATE UNIQUE INDEX strategy_runs_one_running_per_candidate ON strategy_runs(candidate_id) WHERE status='running';
    ALTER TABLE strategy_runs DROP CONSTRAINT strategy_runs_owner_key;
    ALTER TABLE strategy_runs DROP COLUMN original_cumulative_pnl;
    ALTER TABLE strategy_runs DROP COLUMN accounting_note;
    ALTER TABLE strategy_runs DROP COLUMN accounting_status;
    """)
