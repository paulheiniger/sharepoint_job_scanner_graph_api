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

CREATE TABLE IF NOT EXISTS product_document_queue (
    queue_id TEXT PRIMARY KEY,
    source_path TEXT UNIQUE,
    source_url TEXT UNIQUE,
    source_type TEXT DEFAULT 'local_file',
    source_domain TEXT,
    domain_approved BOOLEAN DEFAULT false,
    approved_for_ingest BOOLEAN DEFAULT false,
    review_status TEXT DEFAULT 'pending_review',
    discovery_method TEXT DEFAULT 'manual',
    manufacturer_hint TEXT,
    document_type TEXT,
    discovered_at TIMESTAMPTZ DEFAULT now(),
    ingest_status TEXT DEFAULT 'pending',
    product_id TEXT REFERENCES product_catalog(product_id) ON DELETE SET NULL,
    catalog_path TEXT,
    content_hash TEXT,
    decision_nodes JSONB DEFAULT '[]'::jsonb,
    lookup_ids JSONB DEFAULT '[]'::jsonb,
    source_page_url TEXT,
    link_text TEXT,
    scrape_score NUMERIC,
    priority INTEGER DEFAULT 100,
    fetched_at TIMESTAMPTZ,
    last_checked_at TIMESTAMPTZ,
    validation_warnings JSONB DEFAULT '[]'::jsonb,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS product_family_lookup (
    lookup_id TEXT PRIMARY KEY,
    vendor TEXT,
    canonical_product_family TEXT,
    template_option TEXT,
    cell_type TEXT,
    density_class TEXT,
    application_hint TEXT,
    lookup_priority TEXT,
    lookup_terms TEXT,
    preferred_documents TEXT,
    official_vendor_url TEXT,
    source_domain TEXT,
    domain_approved BOOLEAN DEFAULT false,
    decision_nodes JSONB DEFAULT '[]'::jsonb,
    priority INTEGER DEFAULT 50,
    active BOOLEAN DEFAULT true,
    status TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_product_catalog_name ON product_catalog(product_name);
CREATE INDEX IF NOT EXISTS idx_product_aliases_product ON product_aliases(product_id);
CREATE INDEX IF NOT EXISTS idx_product_documents_product ON product_documents(product_id);
CREATE INDEX IF NOT EXISTS idx_product_properties_product ON product_properties(product_id);
CREATE INDEX IF NOT EXISTS idx_product_rules_product ON product_rules(product_id);
CREATE INDEX IF NOT EXISTS idx_product_decision_links_product ON product_decision_links(product_id);
CREATE INDEX IF NOT EXISTS idx_product_decision_links_decision ON product_decision_links(decision_id);
CREATE INDEX IF NOT EXISTS idx_product_document_queue_status ON product_document_queue(ingest_status);
CREATE INDEX IF NOT EXISTS idx_product_family_lookup_vendor ON product_family_lookup(vendor);
CREATE INDEX IF NOT EXISTS idx_product_family_lookup_domain ON product_family_lookup(source_domain);

ALTER TABLE product_catalog ADD COLUMN IF NOT EXISTS extraction_method TEXT;
ALTER TABLE product_catalog ADD COLUMN IF NOT EXISTS extraction_warnings JSONB DEFAULT '[]'::jsonb;
ALTER TABLE product_documents ADD COLUMN IF NOT EXISTS extraction_method TEXT;
ALTER TABLE product_documents ADD COLUMN IF NOT EXISTS extraction_warnings JSONB DEFAULT '[]'::jsonb;
ALTER TABLE product_properties ADD COLUMN IF NOT EXISTS numeric_min NUMERIC;
ALTER TABLE product_properties ADD COLUMN IF NOT EXISTS numeric_max NUMERIC;
ALTER TABLE product_document_queue ALTER COLUMN source_path DROP NOT NULL;
ALTER TABLE product_document_queue ADD COLUMN IF NOT EXISTS manufacturer_hint TEXT;
ALTER TABLE product_document_queue ADD COLUMN IF NOT EXISTS source_url TEXT;
ALTER TABLE product_document_queue ADD COLUMN IF NOT EXISTS source_domain TEXT;
ALTER TABLE product_document_queue ADD COLUMN IF NOT EXISTS domain_approved BOOLEAN DEFAULT false;
ALTER TABLE product_document_queue ADD COLUMN IF NOT EXISTS approved_for_ingest BOOLEAN DEFAULT false;
ALTER TABLE product_document_queue ADD COLUMN IF NOT EXISTS review_status TEXT DEFAULT 'pending_review';
ALTER TABLE product_document_queue ADD COLUMN IF NOT EXISTS discovery_method TEXT DEFAULT 'manual';
ALTER TABLE product_document_queue ADD COLUMN IF NOT EXISTS content_hash TEXT;
ALTER TABLE product_document_queue ADD COLUMN IF NOT EXISTS decision_nodes JSONB DEFAULT '[]'::jsonb;
ALTER TABLE product_document_queue ADD COLUMN IF NOT EXISTS lookup_ids JSONB DEFAULT '[]'::jsonb;
ALTER TABLE product_document_queue ADD COLUMN IF NOT EXISTS source_page_url TEXT;
ALTER TABLE product_document_queue ADD COLUMN IF NOT EXISTS link_text TEXT;
ALTER TABLE product_document_queue ADD COLUMN IF NOT EXISTS scrape_score NUMERIC;
ALTER TABLE product_document_queue ADD COLUMN IF NOT EXISTS priority INTEGER DEFAULT 100;
ALTER TABLE product_document_queue ADD COLUMN IF NOT EXISTS fetched_at TIMESTAMPTZ;
ALTER TABLE product_document_queue ADD COLUMN IF NOT EXISTS last_checked_at TIMESTAMPTZ;
ALTER TABLE product_document_queue ADD COLUMN IF NOT EXISTS validation_warnings JSONB DEFAULT '[]'::jsonb;
CREATE UNIQUE INDEX IF NOT EXISTS idx_product_document_queue_source_url ON product_document_queue(source_url) WHERE source_url IS NOT NULL AND source_url <> '';
ALTER TABLE product_family_lookup ADD COLUMN IF NOT EXISTS source_domain TEXT;
ALTER TABLE product_family_lookup ADD COLUMN IF NOT EXISTS template_option TEXT;
ALTER TABLE product_family_lookup ADD COLUMN IF NOT EXISTS cell_type TEXT;
ALTER TABLE product_family_lookup ADD COLUMN IF NOT EXISTS density_class TEXT;
ALTER TABLE product_family_lookup ADD COLUMN IF NOT EXISTS application_hint TEXT;
ALTER TABLE product_family_lookup ADD COLUMN IF NOT EXISTS lookup_priority TEXT;
ALTER TABLE product_family_lookup ADD COLUMN IF NOT EXISTS preferred_documents TEXT;
ALTER TABLE product_family_lookup ADD COLUMN IF NOT EXISTS domain_approved BOOLEAN DEFAULT false;
ALTER TABLE product_family_lookup ADD COLUMN IF NOT EXISTS decision_nodes JSONB DEFAULT '[]'::jsonb;
ALTER TABLE product_family_lookup ADD COLUMN IF NOT EXISTS priority INTEGER DEFAULT 50;
ALTER TABLE product_family_lookup ADD COLUMN IF NOT EXISTS active BOOLEAN DEFAULT true;
ALTER TABLE product_family_lookup ADD COLUMN IF NOT EXISTS status TEXT;
ALTER TABLE product_family_lookup ADD COLUMN IF NOT EXISTS notes TEXT;
ALTER TABLE product_family_lookup ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now();
