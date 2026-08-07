BEGIN;

CREATE TABLE IF NOT EXISTS quickbooks_connections (
    realm_id TEXT PRIMARY KEY,
    company_name TEXT NOT NULL,
    environment TEXT NOT NULL CHECK (environment IN ('sandbox', 'production')),
    access_token_encrypted TEXT NOT NULL,
    refresh_token_encrypted TEXT NOT NULL,
    access_token_expires_at TIMESTAMPTZ,
    refresh_token_expires_at TIMESTAMPTZ,
    scope TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'connected',
    last_sync_at TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS quickbooks_oauth_states (
    nonce_hash TEXT PRIMARY KEY,
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS quickbooks_customers (
    realm_id TEXT NOT NULL,
    quickbooks_id TEXT NOT NULL,
    sync_token TEXT,
    display_name TEXT,
    company_name TEXT,
    fully_qualified_name TEXT,
    parent_ref TEXT,
    active BOOLEAN,
    balance DOUBLE PRECISION,
    currency TEXT,
    email TEXT,
    phone TEXT,
    source_created_at TIMESTAMPTZ,
    source_updated_at TIMESTAMPTZ,
    synced_at TIMESTAMPTZ NOT NULL,
    raw_json TEXT NOT NULL,
    PRIMARY KEY (realm_id, quickbooks_id)
);

CREATE TABLE IF NOT EXISTS quickbooks_sales_transactions (
    realm_id TEXT NOT NULL,
    entity_type TEXT NOT NULL CHECK (entity_type IN ('Estimate', 'Invoice', 'CreditMemo')),
    quickbooks_id TEXT NOT NULL,
    sync_token TEXT,
    txn_date TIMESTAMPTZ,
    doc_number TEXT,
    customer_ref TEXT,
    customer_name TEXT,
    due_date TIMESTAMPTZ,
    total_amount DOUBLE PRECISION,
    balance DOUBLE PRECISION,
    currency TEXT,
    status TEXT,
    linked_transactions_json TEXT NOT NULL DEFAULT '[]',
    source_created_at TIMESTAMPTZ,
    source_updated_at TIMESTAMPTZ,
    synced_at TIMESTAMPTZ NOT NULL,
    raw_json TEXT NOT NULL,
    PRIMARY KEY (realm_id, entity_type, quickbooks_id)
);

CREATE TABLE IF NOT EXISTS quickbooks_payments (
    realm_id TEXT NOT NULL,
    quickbooks_id TEXT NOT NULL,
    sync_token TEXT,
    txn_date TIMESTAMPTZ,
    customer_ref TEXT,
    customer_name TEXT,
    total_amount DOUBLE PRECISION,
    unapplied_amount DOUBLE PRECISION,
    currency TEXT,
    linked_transactions_json TEXT NOT NULL DEFAULT '[]',
    source_created_at TIMESTAMPTZ,
    source_updated_at TIMESTAMPTZ,
    synced_at TIMESTAMPTZ NOT NULL,
    raw_json TEXT NOT NULL,
    PRIMARY KEY (realm_id, quickbooks_id)
);

CREATE TABLE IF NOT EXISTS quickbooks_sync_state (
    realm_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    last_source_updated_at TIMESTAMPTZ,
    last_started_at TIMESTAMPTZ,
    last_completed_at TIMESTAMPTZ,
    status TEXT NOT NULL,
    records_processed INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    PRIMARY KEY (realm_id, entity_type)
);

CREATE TABLE IF NOT EXISTS quickbooks_job_links (
    realm_id TEXT NOT NULL,
    quickbooks_customer_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    match_method TEXT NOT NULL,
    confidence DOUBLE PRECISION,
    reviewed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (realm_id, quickbooks_customer_id, job_id)
);

CREATE TABLE IF NOT EXISTS quickbooks_webhook_events (
    event_hash TEXT PRIMARY KEY,
    realm_id TEXT,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMPTZ,
    last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_quickbooks_customers_name
    ON quickbooks_customers (realm_id, display_name);
CREATE INDEX IF NOT EXISTS idx_quickbooks_transactions_customer
    ON quickbooks_sales_transactions (realm_id, customer_ref, txn_date DESC);
CREATE INDEX IF NOT EXISTS idx_quickbooks_transactions_due
    ON quickbooks_sales_transactions (realm_id, entity_type, due_date, balance);
CREATE INDEX IF NOT EXISTS idx_quickbooks_payments_customer
    ON quickbooks_payments (realm_id, customer_ref, txn_date DESC);

COMMIT;
