PRAGMA foreign_keys=ON;
CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE source(
 source_id TEXT PRIMARY KEY, kind TEXT NOT NULL, external_key TEXT NOT NULL,
 content_sha256 TEXT NOT NULL, file_sha256 TEXT, parent_source_id TEXT REFERENCES source(source_id),
 locator TEXT, source_at TEXT, observed_at TEXT NOT NULL, scope TEXT NOT NULL,
 direction TEXT NOT NULL, parse_state TEXT NOT NULL, extract_state TEXT NOT NULL,
 failure_reason TEXT, coverage_json TEXT NOT NULL, private_ref TEXT,
 UNIQUE(kind,external_key,content_sha256)
);
CREATE TABLE supplier(
 supplier_id TEXT PRIMARY KEY,name TEXT NOT NULL,
 supplier_type TEXT NOT NULL CHECK(supplier_type IN ('产品','物流','配套')),
 categories_json TEXT NOT NULL,contacts_json TEXT NOT NULL,reliability_notes TEXT NOT NULL,
 aliases_json TEXT NOT NULL,field_sources_json TEXT NOT NULL
);
CREATE TABLE product(
 product_id TEXT PRIMARY KEY,product_family TEXT NOT NULL,category TEXT NOT NULL,
 canonical_model TEXT,display_name TEXT NOT NULL,variant_label TEXT NOT NULL,
 config_json TEXT NOT NULL,config_signature TEXT NOT NULL,
 evidence_state TEXT NOT NULL,field_sources_json TEXT NOT NULL,
 UNIQUE(product_family,config_signature)
);
CREATE TABLE supplier_product(
 supplier_id TEXT NOT NULL REFERENCES supplier(supplier_id),
 product_id TEXT NOT NULL REFERENCES product(product_id),
 model_aliases_json TEXT NOT NULL,evidence_json TEXT NOT NULL,
 PRIMARY KEY(supplier_id,product_id)
);
CREATE TABLE offer(
 offer_id TEXT PRIMARY KEY,supplier_id TEXT NOT NULL REFERENCES supplier(supplier_id),
 product_id TEXT REFERENCES product(product_id),
 amount_decimal TEXT,amount_raw TEXT NOT NULL,currency TEXT,
 tax_basis TEXT NOT NULL DEFAULT 'unknown',tax_rate_text TEXT,unit TEXT,
 price_kind TEXT NOT NULL,quote_state TEXT NOT NULL,
 moq_text TEXT,quantity_condition TEXT,lead_time_text TEXT,
 packing_json TEXT NOT NULL,conditions_json TEXT NOT NULL,
 quoted_on TEXT,quoted_on_basis TEXT NOT NULL,document_period TEXT,valid_until TEXT,
 supersedes_offer_id TEXT REFERENCES offer(offer_id),
 source_id TEXT NOT NULL REFERENCES source(source_id),locator TEXT NOT NULL,
 field_sources_json TEXT NOT NULL,dedup_key TEXT NOT NULL UNIQUE,
 FOREIGN KEY(supplier_id,product_id) REFERENCES supplier_product(supplier_id,product_id)
);
CREATE TABLE evidence(
 evidence_id TEXT PRIMARY KEY,entity_kind TEXT NOT NULL,entity_id TEXT NOT NULL,
 field_path TEXT NOT NULL,source_id TEXT NOT NULL REFERENCES source(source_id),
 locator TEXT NOT NULL,excerpt TEXT NOT NULL,bbox_json TEXT,
 method TEXT NOT NULL,evidence_state TEXT NOT NULL,
 UNIQUE(entity_kind,entity_id,field_path,source_id,locator,excerpt)
);
CREATE TABLE gap(
 gap_id TEXT PRIMARY KEY,supplier_id TEXT NOT NULL REFERENCES supplier(supplier_id),
 product_id TEXT REFERENCES product(product_id),offer_id TEXT REFERENCES offer(offer_id),
 gap_type TEXT NOT NULL,priority INTEGER NOT NULL,impact TEXT NOT NULL,
 ask_text TEXT NOT NULL,source_refs_json TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'open'
);
CREATE TABLE legacy_map(
 legacy_store TEXT NOT NULL,legacy_table TEXT NOT NULL,legacy_key TEXT NOT NULL,
 entity_kind TEXT NOT NULL,entity_id TEXT,state TEXT NOT NULL,reason TEXT NOT NULL,
 PRIMARY KEY(legacy_store,legacy_table,legacy_key)
);
CREATE TABLE merge_event(
 merge_id TEXT PRIMARY KEY,entity_kind TEXT NOT NULL,from_ref TEXT NOT NULL,
 to_ref TEXT NOT NULL,rule TEXT NOT NULL,evidence_json TEXT NOT NULL,state TEXT NOT NULL
);
CREATE INDEX source_state_idx ON source(kind,parse_state,extract_state);
CREATE INDEX product_family_idx ON product(product_family);
CREATE INDEX offer_supplier_product_idx ON offer(supplier_id,product_id,quoted_on);
CREATE INDEX evidence_entity_idx ON evidence(entity_kind,entity_id,field_path);
CREATE INDEX gap_priority_idx ON gap(priority,supplier_id);
