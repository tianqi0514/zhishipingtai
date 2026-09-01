CREATE TABLE IF NOT EXISTS data_source_schema_versions (
  id VARCHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  space_id VARCHAR(36) NOT NULL REFERENCES knowledge_spaces(id),
  source_id VARCHAR(36) NOT NULL REFERENCES source_connectors(id),
  version_number INTEGER NOT NULL,
  schema_fingerprint VARCHAR(64) NOT NULL,
  catalog JSON NOT NULL,
  diff_from_previous JSON NOT NULL,
  status VARCHAR(32) NOT NULL,
  object_count INTEGER NOT NULL,
  column_count INTEGER NOT NULL,
  primary_key_count INTEGER NOT NULL,
  foreign_key_count INTEGER NOT NULL,
  discovered_at TIMESTAMP NOT NULL,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  deleted_at TIMESTAMP,
  CONSTRAINT uq_source_schema_version UNIQUE (source_id, version_number),
  CONSTRAINT uq_source_schema_fingerprint UNIQUE (source_id, schema_fingerprint)
);
CREATE INDEX IF NOT EXISTS ix_source_schema_tenant ON data_source_schema_versions(tenant_id);
CREATE INDEX IF NOT EXISTS ix_source_schema_space ON data_source_schema_versions(space_id);
CREATE INDEX IF NOT EXISTS ix_source_schema_source ON data_source_schema_versions(source_id);
CREATE INDEX IF NOT EXISTS ix_source_schema_fingerprint ON data_source_schema_versions(schema_fingerprint);

CREATE TABLE IF NOT EXISTS data_preview_policies (
  id VARCHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  space_id VARCHAR(36) NOT NULL REFERENCES knowledge_spaces(id),
  source_id VARCHAR(36) NOT NULL REFERENCES source_connectors(id),
  live_preview_enabled BOOLEAN NOT NULL,
  allowed_objects JSON NOT NULL,
  denied_objects JSON NOT NULL,
  allowed_columns JSON NOT NULL,
  sensitive_columns JSON NOT NULL,
  masking_rules JSON NOT NULL,
  default_order JSON NOT NULL,
  default_page_size INTEGER NOT NULL,
  max_page_size INTEGER NOT NULL,
  max_text_length INTEGER NOT NULL,
  allow_full_cell BOOLEAN NOT NULL,
  allow_exact_count BOOLEAN NOT NULL,
  query_timeout_seconds INTEGER NOT NULL,
  max_filter_conditions INTEGER NOT NULL,
  max_result_bytes INTEGER NOT NULL,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  deleted_at TIMESTAMP,
  CONSTRAINT uq_data_preview_policy_source UNIQUE (source_id)
);
CREATE INDEX IF NOT EXISTS ix_data_preview_policy_tenant ON data_preview_policies(tenant_id);
CREATE INDEX IF NOT EXISTS ix_data_preview_policy_space ON data_preview_policies(space_id);

CREATE TABLE IF NOT EXISTS semantic_mapping_sets (
  id VARCHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  space_id VARCHAR(36) NOT NULL REFERENCES knowledge_spaces(id),
  source_id VARCHAR(36) NOT NULL REFERENCES source_connectors(id),
  ontology_id VARCHAR(36) NOT NULL REFERENCES ontologies(id),
  name VARCHAR(200) NOT NULL,
  description TEXT NOT NULL,
  status VARCHAR(32) NOT NULL,
  active_version_id VARCHAR(36),
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  deleted_at TIMESTAMP,
  CONSTRAINT uq_semantic_mapping_source_name UNIQUE (source_id, name)
);
CREATE INDEX IF NOT EXISTS ix_semantic_mapping_set_tenant ON semantic_mapping_sets(tenant_id);
CREATE INDEX IF NOT EXISTS ix_semantic_mapping_set_space ON semantic_mapping_sets(space_id);
CREATE INDEX IF NOT EXISTS ix_semantic_mapping_set_source ON semantic_mapping_sets(source_id);
CREATE INDEX IF NOT EXISTS ix_semantic_mapping_set_ontology ON semantic_mapping_sets(ontology_id);

CREATE TABLE IF NOT EXISTS semantic_mapping_versions (
  id VARCHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  space_id VARCHAR(36) NOT NULL REFERENCES knowledge_spaces(id),
  source_id VARCHAR(36) NOT NULL REFERENCES source_connectors(id),
  mapping_set_id VARCHAR(36) NOT NULL REFERENCES semantic_mapping_sets(id),
  schema_version_id VARCHAR(36) NOT NULL REFERENCES data_source_schema_versions(id),
  schema_fingerprint VARCHAR(64) NOT NULL,
  mapping_hash VARCHAR(64) NOT NULL,
  version_number INTEGER NOT NULL,
  manifest JSON NOT NULL,
  status VARCHAR(32) NOT NULL,
  validation_report JSON NOT NULL,
  created_by VARCHAR(36) NOT NULL REFERENCES users(id),
  activated_by VARCHAR(36) REFERENCES users(id),
  activated_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  deleted_at TIMESTAMP,
  CONSTRAINT uq_semantic_mapping_version UNIQUE (mapping_set_id, version_number),
  CONSTRAINT uq_semantic_mapping_hash UNIQUE (mapping_set_id, mapping_hash)
);
CREATE INDEX IF NOT EXISTS ix_semantic_mapping_version_set ON semantic_mapping_versions(mapping_set_id);
CREATE INDEX IF NOT EXISTS ix_semantic_mapping_version_source ON semantic_mapping_versions(source_id);
CREATE INDEX IF NOT EXISTS ix_semantic_mapping_version_schema ON semantic_mapping_versions(schema_version_id);
CREATE INDEX IF NOT EXISTS ix_semantic_mapping_version_status ON semantic_mapping_versions(status);

CREATE TABLE IF NOT EXISTS structured_query_runs (
  id VARCHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  space_id VARCHAR(36) NOT NULL REFERENCES knowledge_spaces(id),
  source_id VARCHAR(36) NOT NULL REFERENCES source_connectors(id),
  user_id VARCHAR(36) NOT NULL REFERENCES users(id),
  mapping_version_id VARCHAR(36) REFERENCES semantic_mapping_versions(id),
  schema_version_id VARCHAR(36) NOT NULL REFERENCES data_source_schema_versions(id),
  conversation_id VARCHAR(36) REFERENCES conversations(id),
  message_id VARCHAR(36) REFERENCES conversation_messages(id),
  original_question TEXT NOT NULL,
  semantic_plan JSON NOT NULL,
  plan_fingerprint VARCHAR(64),
  query_ir JSON NOT NULL,
  ir_fingerprint VARCHAR(64),
  dialect VARCHAR(32) NOT NULL,
  sql_template TEXT NOT NULL,
  parameter_summary JSON NOT NULL,
  referenced_objects JSON NOT NULL,
  referenced_columns JSON NOT NULL,
  result_columns JSON NOT NULL,
  result_rows JSON NOT NULL,
  row_count INTEGER NOT NULL,
  result_bytes INTEGER NOT NULL,
  truncated BOOLEAN NOT NULL,
  elapsed_ms INTEGER,
  status VARCHAR(32) NOT NULL,
  warnings JSON NOT NULL,
  error_code VARCHAR(100),
  error_message TEXT,
  cancel_requested_at TIMESTAMP,
  started_at TIMESTAMP,
  finished_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  deleted_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_structured_query_run_tenant ON structured_query_runs(tenant_id);
CREATE INDEX IF NOT EXISTS ix_structured_query_run_user ON structured_query_runs(user_id);
CREATE INDEX IF NOT EXISTS ix_structured_query_run_source ON structured_query_runs(source_id);
CREATE INDEX IF NOT EXISTS ix_structured_query_run_status ON structured_query_runs(status);

CREATE TABLE IF NOT EXISTS structured_query_citations (
  id VARCHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  query_run_id VARCHAR(36) NOT NULL REFERENCES structured_query_runs(id),
  message_id VARCHAR(36) REFERENCES conversation_messages(id),
  citation_number INTEGER NOT NULL,
  label VARCHAR(500) NOT NULL,
  summary JSON NOT NULL,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  deleted_at TIMESTAMP,
  CONSTRAINT uq_structured_query_citation UNIQUE (query_run_id, citation_number)
);
CREATE INDEX IF NOT EXISTS ix_structured_query_citation_run ON structured_query_citations(query_run_id);
