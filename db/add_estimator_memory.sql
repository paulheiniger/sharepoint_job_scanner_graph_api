CREATE TABLE IF NOT EXISTS estimator_memory (
    memory_id UUID PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status TEXT NOT NULL DEFAULT 'pending',
    priority TEXT NOT NULL DEFAULT 'medium',
    template_type TEXT,
    decision_id TEXT,
    template_bucket TEXT,
    product_or_system TEXT,
    applies_when JSONB DEFAULT '{}'::jsonb,
    guidance TEXT NOT NULL,
    rationale TEXT,
    source_type TEXT,
    source_session_id UUID,
    source_edit_id UUID,
    approved_by TEXT,
    approved_at TIMESTAMPTZ,
    usage_count INTEGER NOT NULL DEFAULT 0,
    last_used_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_estimator_memory_status_template
    ON estimator_memory(status, template_type);

CREATE INDEX IF NOT EXISTS idx_estimator_memory_bucket
    ON estimator_memory(template_bucket);
