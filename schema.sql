-- Argus MVP schema — SQLite / Postgres compatible (avoid PG-only types)
-- The Trade Exception Corpus: expert-annotated, cross-verified shipment dossiers

CREATE TABLE IF NOT EXISTS taxonomy_codes (
    code        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    definition  TEXT NOT NULL,
    documents_compared TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'active'  -- active | proposed
);

CREATE TABLE IF NOT EXISTS contributors (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    contributor_code TEXT NOT NULL UNIQUE,   -- anonymized, e.g. FWD-A
    role             TEXT NOT NULL,          -- forwarder | broker | verifier
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS dossiers (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    reference          TEXT NOT NULL UNIQUE,
    origin             TEXT NOT NULL,
    destination        TEXT NOT NULL,
    commodity          TEXT NOT NULL,
    hs_code            TEXT,
    incoterm           TEXT,
    shipment_date      TEXT,                 -- ISO date
    declared_value_usd INTEGER,
    freight_cost_usd   REAL,
    insured_value_usd  REAL,
    gross_weight_kg    REAL,
    quantity_pcs       INTEGER,
    unit_price_usd     REAL,
    vintage_year       INTEGER,
    regime_note        TEXT,                 -- regulatory regime at ship date
    synthetic          INTEGER NOT NULL DEFAULT 0,  -- 1 = synthetic demo data
    status             TEXT NOT NULL DEFAULT 'draft', -- draft | annotated | verified
    ingested_date      TEXT,
    redacted           INTEGER NOT NULL DEFAULT 0,
    audit_sampled      INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS documents (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    dossier_id INTEGER NOT NULL REFERENCES dossiers(id),
    doc_type   TEXT NOT NULL,   -- commercial_invoice | packing_list | bill_of_lading | certificate_of_origin | export_declaration | insurance_certificate
    file_ref   TEXT,            -- storage pointer; NULL for synthetic
    redacted   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS annotations (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    annotation_ref   TEXT UNIQUE,
    dossier_id       INTEGER NOT NULL REFERENCES dossiers(id),
    contributor_id   INTEGER NOT NULL REFERENCES contributors(id),
    exception_code   TEXT NOT NULL REFERENCES taxonomy_codes(code),
    definition       TEXT,
    documents_compared TEXT,
    decision         TEXT,
    cost_impact_usd  REAL,
    resolution       TEXT,
    annotation_status TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS verifications (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    annotation_id         INTEGER NOT NULL REFERENCES annotations(id),
    verifier_contributor_id INTEGER NOT NULL REFERENCES contributors(id),
    verdict               TEXT NOT NULL,  -- confirmed | rejected | needs_work
    notes                 TEXT,
    created_at            TEXT NOT NULL DEFAULT (datetime('now'))
);
-- Business rule (enforced in the API, not expressible as a CHECK across tables):
-- verifier_contributor_id MUST differ from the annotation's contributor_id.

CREATE TABLE IF NOT EXISTS agreement_acceptances (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    contributor_id INTEGER NOT NULL REFERENCES contributors(id),
    agreement_version TEXT NOT NULL,
    accepted_at    TEXT NOT NULL DEFAULT (datetime('now')),
    signer_name    TEXT,
    signer_firm    TEXT
);

CREATE TABLE IF NOT EXISTS cbp_rulings (
    ruling_number          TEXT PRIMARY KEY,
    date                   TEXT,
    product_description    TEXT,
    hts_classification     TEXT,
    requester_proposed_hts TEXT,
    reasoning_summary      TEXT,
    country_of_origin      TEXT,
    source_url             TEXT,
    license                TEXT NOT NULL DEFAULT 'public-domain'  -- US government work
);

CREATE TABLE IF NOT EXISTS trade_benchmarks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    country        TEXT NOT NULL,
    partner_country TEXT,
    hs_code        TEXT NOT NULL,
    hs_level       INTEGER,
    period         TEXT,
    value_usd      REAL,
    quantity       REAL,
    unit           TEXT,
    source_name    TEXT NOT NULL,
    source_url     TEXT,
    as_of_date     TEXT,
    license        TEXT
);

-- Seed: the 10-code exception taxonomy (v0.1)
INSERT OR IGNORE INTO taxonomy_codes (code, name, definition, documents_compared, status) VALUES
('VALUE_MISMATCH', 'Value mismatch', 'Declared value differs across documents (e.g. invoice vs letter of credit).', 'commercial_invoice, letter_of_credit', 'active'),
('QUANTITY_VARIANCE', 'Quantity variance', 'Shipped quantity differs between packing list, B/L, or invoice.', 'packing_list, bill_of_lading, commercial_invoice', 'active'),
('HS_MISCLASSIFICATION', 'HS misclassification', 'Wrong tariff code declared for the commodity.', 'commercial_invoice, export_declaration', 'active'),
('LATE_SHIPMENT', 'Late shipment', 'Shipment date falls after the LC shipment deadline.', 'bill_of_lading, letter_of_credit', 'active'),
('COO_DEFECT', 'Certificate of origin defect', 'COO missing, invalid, or inconsistent with preference claim.', 'certificate_of_origin, commercial_invoice', 'active'),
('WEIGHT_DISCREPANCY', 'Weight discrepancy', 'Declared weight differs from actual/weighed figures across documents.', 'packing_list, bill_of_lading, commercial_invoice', 'active'),
('DOC_INCONSISTENCY', 'Document inconsistency', 'Names, ports, marks, or references differ across documents in the set.', 'all_documents', 'active'),
('INSURANCE_GAP', 'Insurance gap', 'Insured value does not cover declared value.', 'insurance_certificate, commercial_invoice', 'active'),
('LC_TERM_DISCREPANCY', 'LC term discrepancy', 'Letter of credit terms conflict with presented documents.', 'letter_of_credit, all_documents', 'proposed'),
('FREIGHT_SURCHARGE_VARIANCE', 'Freight surcharge variance', 'Freight charges differ from agreed or quoted rates.', 'bill_of_lading, freight_quotation', 'proposed');

CREATE TABLE IF NOT EXISTS expert_leads (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name TEXT NOT NULL,
    last_name  TEXT NOT NULL,
    company    TEXT NOT NULL,
    phone      TEXT NOT NULL,
    email      TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT 'landing',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS shipments (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_ref     TEXT NOT NULL,
    contributor_code TEXT NOT NULL,   -- numerical code: revenue-share key
    status           TEXT NOT NULL DEFAULT 'collecting',  -- collecting | ready | submitted
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS shipment_documents (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_id INTEGER NOT NULL REFERENCES shipments(id),
    job_id     TEXT NOT NULL UNIQUE,  -- redact_jobs/{job_id}
    doc_type   TEXT NOT NULL,         -- one of the 8 canonical document types
    filename   TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
