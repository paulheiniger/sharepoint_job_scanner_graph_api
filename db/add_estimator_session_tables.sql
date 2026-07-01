CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS estimator_sessions (
    session_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    division TEXT,
    template_type TEXT,
    customer TEXT,
    job_name TEXT,
    site_address TEXT,
    raw_input_notes TEXT,
    input_source_type TEXT,
    photos_present BOOLEAN DEFAULT false,
    source_file_ids JSONB DEFAULT '[]'::jsonb,
    ai_model TEXT,
    estimate_status TEXT,
    exported_workbook_path TEXT,
    exported_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS estimator_scope_interpretations (
    interpretation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES estimator_sessions(session_id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    parsed_scope JSONB DEFAULT '{}'::jsonb,
    deterministic_scope JSONB DEFAULT '{}'::jsonb,
    assumptions JSONB DEFAULT '{}'::jsonb,
    missing_questions JSONB DEFAULT '[]'::jsonb,
    confidence_by_field JSONB DEFAULT '{}'::jsonb,
    review_flags JSONB DEFAULT '[]'::jsonb
);

CREATE TABLE IF NOT EXISTS estimator_decision_proposals (
    proposal_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES estimator_sessions(session_id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    decision_graph_version TEXT,
    template_type TEXT,
    proposed_decisions JSONB DEFAULT '{}'::jsonb,
    proposal_source TEXT,
    evidence_summary JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS estimator_decision_edits (
    edit_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES estimator_sessions(session_id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    decision_id TEXT,
    field_name TEXT,
    old_value JSONB,
    new_value JSONB,
    edited_by TEXT,
    edit_reason TEXT
);

CREATE TABLE IF NOT EXISTS estimator_final_decisions (
    final_decision_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES estimator_sessions(session_id) ON DELETE CASCADE,
    exported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    decision_graph_version TEXT,
    final_decisions JSONB DEFAULT '{}'::jsonb,
    calculated_outputs JSONB DEFAULT '{}'::jsonb,
    workbook_cell_writes JSONB DEFAULT '[]'::jsonb,
    workbook_export_path TEXT
);

CREATE TABLE IF NOT EXISTS estimator_session_artifacts (
    artifact_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES estimator_sessions(session_id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    artifact_type TEXT,
    artifact_path TEXT,
    artifact_json JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_estimator_scope_interpretations_session
    ON estimator_scope_interpretations(session_id);
CREATE INDEX IF NOT EXISTS idx_estimator_decision_proposals_session
    ON estimator_decision_proposals(session_id);
CREATE INDEX IF NOT EXISTS idx_estimator_decision_edits_session
    ON estimator_decision_edits(session_id);
CREATE INDEX IF NOT EXISTS idx_estimator_final_decisions_session
    ON estimator_final_decisions(session_id);
CREATE INDEX IF NOT EXISTS idx_estimator_session_artifacts_session
    ON estimator_session_artifacts(session_id);
