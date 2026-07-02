CREATE TABLE IF NOT EXISTS product_catalog (
    product_id TEXT PRIMARY KEY,
    manufacturer TEXT,
    product_family TEXT,
    product_name TEXT NOT NULL,
    sku TEXT,
    category TEXT,
    subcategory TEXT,
    unit TEXT,
    aliases JSONB DEFAULT '[]'::jsonb,
    active BOOLEAN DEFAULT true,
    extraction_method TEXT,
    extraction_warnings JSONB DEFAULT '[]'::jsonb
);

CREATE TABLE IF NOT EXISTS product_aliases (
    alias_id TEXT PRIMARY KEY,
    product_id TEXT REFERENCES product_catalog(product_id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    alias_type TEXT,
    confidence NUMERIC
);

CREATE TABLE IF NOT EXISTS product_documents (
    document_id TEXT PRIMARY KEY,
    product_id TEXT REFERENCES product_catalog(product_id) ON DELETE SET NULL,
    document_type TEXT,
    source_type TEXT,
    source_path TEXT,
    revision_date DATE,
    extracted_at TIMESTAMPTZ DEFAULT now(),
    raw_text_hash TEXT,
    extraction_method TEXT,
    extraction_warnings JSONB DEFAULT '[]'::jsonb
);

CREATE TABLE IF NOT EXISTS product_properties (
    property_id TEXT PRIMARY KEY,
    product_id TEXT REFERENCES product_catalog(product_id) ON DELETE CASCADE,
    document_id TEXT REFERENCES product_documents(document_id) ON DELETE SET NULL,
    property_name TEXT NOT NULL,
    property_value TEXT,
    numeric_value NUMERIC,
    numeric_min NUMERIC,
    numeric_max NUMERIC,
    unit TEXT,
    source_page INTEGER,
    source_text TEXT,
    confidence NUMERIC
);

CREATE TABLE IF NOT EXISTS product_rules (
    rule_id TEXT PRIMARY KEY,
    product_id TEXT REFERENCES product_catalog(product_id) ON DELETE CASCADE,
    document_id TEXT REFERENCES product_documents(document_id) ON DELETE SET NULL,
    rule_type TEXT NOT NULL,
    rule_value TEXT,
    source_page INTEGER,
    source_text TEXT,
    confidence NUMERIC,
    severity TEXT
);

CREATE TABLE IF NOT EXISTS product_decision_links (
    link_id TEXT PRIMARY KEY,
    product_id TEXT REFERENCES product_catalog(product_id) ON DELETE CASCADE,
    decision_id TEXT NOT NULL,
    influence_type TEXT,
    confidence NUMERIC,
    reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_product_catalog_name ON product_catalog(product_name);
CREATE INDEX IF NOT EXISTS idx_product_aliases_product ON product_aliases(product_id);
CREATE INDEX IF NOT EXISTS idx_product_documents_product ON product_documents(product_id);
CREATE INDEX IF NOT EXISTS idx_product_properties_product ON product_properties(product_id);
CREATE INDEX IF NOT EXISTS idx_product_rules_product ON product_rules(product_id);
CREATE INDEX IF NOT EXISTS idx_product_decision_links_product ON product_decision_links(product_id);
CREATE INDEX IF NOT EXISTS idx_product_decision_links_decision ON product_decision_links(decision_id);

ALTER TABLE product_catalog ADD COLUMN IF NOT EXISTS extraction_method TEXT;
ALTER TABLE product_catalog ADD COLUMN IF NOT EXISTS extraction_warnings JSONB DEFAULT '[]'::jsonb;
ALTER TABLE product_documents ADD COLUMN IF NOT EXISTS extraction_method TEXT;
ALTER TABLE product_documents ADD COLUMN IF NOT EXISTS extraction_warnings JSONB DEFAULT '[]'::jsonb;
ALTER TABLE product_properties ADD COLUMN IF NOT EXISTS numeric_min NUMERIC;
ALTER TABLE product_properties ADD COLUMN IF NOT EXISTS numeric_max NUMERIC;
