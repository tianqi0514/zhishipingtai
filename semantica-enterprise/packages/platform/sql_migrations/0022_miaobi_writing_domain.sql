CREATE TABLE IF NOT EXISTS scenario_packages (
  id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  code VARCHAR(100) NOT NULL, name VARCHAR(200) NOT NULL, disaster_type VARCHAR(64) NOT NULL,
  description TEXT NOT NULL, status VARCHAR(32) NOT NULL, current_version_id VARCHAR(36), enabled BOOLEAN NOT NULL,
  created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL, deleted_at TIMESTAMP,
  CONSTRAINT uq_scenario_package_code UNIQUE (tenant_id, code)
);
CREATE TABLE IF NOT EXISTS scenario_package_versions (
  id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  scenario_package_id VARCHAR(36) NOT NULL REFERENCES scenario_packages(id), version INTEGER NOT NULL,
  input_schema JSON NOT NULL, ontology_mapping JSON NOT NULL, rule_set_ids JSON NOT NULL,
  formula_ids JSON NOT NULL, tool_ids JSON NOT NULL, chapter_template JSON NOT NULL,
  output_schema JSON NOT NULL, review_rules JSON NOT NULL, decision_gates JSON NOT NULL,
  comparison_dimensions JSON NOT NULL, config JSON NOT NULL, checksum VARCHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL, created_by VARCHAR(36) NOT NULL REFERENCES users(id),
  created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL, deleted_at TIMESTAMP,
  CONSTRAINT uq_scenario_package_version UNIQUE (scenario_package_id, version)
);
CREATE TABLE IF NOT EXISTS writing_projects (
  id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id), code VARCHAR(100) NOT NULL,
  name VARCHAR(300) NOT NULL, application_id VARCHAR(36) REFERENCES applications(id),
  scenario_package_version_id VARCHAR(36) NOT NULL REFERENCES scenario_package_versions(id),
  knowledge_product_release_id VARCHAR(36) NOT NULL REFERENCES knowledge_product_releases(id),
  owner_id VARCHAR(36) NOT NULL REFERENCES users(id), status VARCHAR(32) NOT NULL, config JSON NOT NULL,
  created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL, deleted_at TIMESTAMP,
  CONSTRAINT uq_writing_project_code UNIQUE (tenant_id, code)
);
CREATE TABLE IF NOT EXISTS writing_project_members (
  id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  project_id VARCHAR(36) NOT NULL REFERENCES writing_projects(id), user_id VARCHAR(36) NOT NULL REFERENCES users(id),
  role VARCHAR(32) NOT NULL, created_by VARCHAR(36) NOT NULL REFERENCES users(id),
  created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL, deleted_at TIMESTAMP,
  CONSTRAINT uq_writing_project_member UNIQUE (project_id, user_id)
);
CREATE TABLE IF NOT EXISTS writing_documents (
  id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  project_id VARCHAR(36) NOT NULL REFERENCES writing_projects(id), title VARCHAR(500) NOT NULL,
  document_type VARCHAR(64) NOT NULL, status VARCHAR(32) NOT NULL, current_version_id VARCHAR(36),
  created_by VARCHAR(36) NOT NULL REFERENCES users(id), created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL, deleted_at TIMESTAMP,
  CONSTRAINT uq_writing_document_title UNIQUE (project_id, title)
);
CREATE TABLE IF NOT EXISTS writing_document_versions (
  id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  document_id VARCHAR(36) NOT NULL REFERENCES writing_documents(id), version INTEGER NOT NULL,
  content JSON NOT NULL, content_hash VARCHAR(64) NOT NULL,
  scenario_package_version_id VARCHAR(36) NOT NULL REFERENCES scenario_package_versions(id),
  knowledge_product_release_id VARCHAR(36) NOT NULL REFERENCES knowledge_product_releases(id),
  status VARCHAR(32) NOT NULL, change_summary TEXT NOT NULL,
  created_by VARCHAR(36) NOT NULL REFERENCES users(id), published_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL, deleted_at TIMESTAMP,
  CONSTRAINT uq_writing_document_version UNIQUE (document_id, version)
);
CREATE TABLE IF NOT EXISTS writing_project_facts (
  id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  project_id VARCHAR(36) NOT NULL REFERENCES writing_projects(id), fact_key VARCHAR(160) NOT NULL,
  label VARCHAR(300) NOT NULL, fact_type VARCHAR(64) NOT NULL, value JSON NOT NULL, unit VARCHAR(64),
  source_type VARCHAR(64) NOT NULL, source_id VARCHAR(100), source_version VARCHAR(100), source_locator JSON NOT NULL,
  confidence FLOAT NOT NULL, verification_status VARCHAR(32) NOT NULL, freshness_status VARCHAR(32) NOT NULL,
  version INTEGER NOT NULL, active BOOLEAN NOT NULL, created_by VARCHAR(36) REFERENCES users(id),
  confirmed_by VARCHAR(36) REFERENCES users(id), confirmed_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL, deleted_at TIMESTAMP,
  CONSTRAINT uq_writing_project_fact_version UNIQUE (project_id, fact_key, version)
);
CREATE TABLE IF NOT EXISTS computation_definitions (
  id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id), code VARCHAR(100) NOT NULL,
  name VARCHAR(200) NOT NULL, description TEXT NOT NULL, current_version_id VARCHAR(36), enabled BOOLEAN NOT NULL,
  created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL, deleted_at TIMESTAMP,
  CONSTRAINT uq_computation_definition_code UNIQUE (tenant_id, code)
);
CREATE TABLE IF NOT EXISTS computation_definition_versions (
  id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  definition_id VARCHAR(36) NOT NULL REFERENCES computation_definitions(id), version INTEGER NOT NULL,
  operation VARCHAR(64) NOT NULL, expression VARCHAR(1000) NOT NULL, input_schema JSON NOT NULL,
  output_schema JSON NOT NULL, unit VARCHAR(64), rounding JSON NOT NULL, default_parameters JSON NOT NULL,
  tests JSON NOT NULL, checksum VARCHAR(64) NOT NULL, status VARCHAR(32) NOT NULL,
  created_by VARCHAR(36) NOT NULL REFERENCES users(id), created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL, deleted_at TIMESTAMP,
  CONSTRAINT uq_computation_definition_version UNIQUE (definition_id, version)
);
CREATE TABLE IF NOT EXISTS computation_runs (
  id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  project_id VARCHAR(36) NOT NULL REFERENCES writing_projects(id),
  definition_version_id VARCHAR(36) NOT NULL REFERENCES computation_definition_versions(id),
  status VARCHAR(32) NOT NULL, inputs JSON NOT NULL, result JSON NOT NULL, input_fact_ids JSON NOT NULL,
  checksum VARCHAR(64) NOT NULL, error_code VARCHAR(100), error_message TEXT,
  started_at TIMESTAMP NOT NULL, finished_at TIMESTAMP, created_by VARCHAR(36) NOT NULL REFERENCES users(id),
  created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL, deleted_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS writing_block_bindings (
  id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  project_id VARCHAR(36) NOT NULL REFERENCES writing_projects(id),
  document_id VARCHAR(36) NOT NULL REFERENCES writing_documents(id), block_id VARCHAR(100) NOT NULL,
  block_type VARCHAR(64) NOT NULL, source_type VARCHAR(64) NOT NULL, source_id VARCHAR(100), source_version VARCHAR(100),
  knowledge_product_release_id VARCHAR(36) REFERENCES knowledge_product_releases(id),
  chunk_id VARCHAR(36) REFERENCES chunks(id), fact_id VARCHAR(36) REFERENCES writing_project_facts(id),
  inferred_fact_id VARCHAR(36) REFERENCES inferred_facts(id), query_run_id VARCHAR(36) REFERENCES structured_query_runs(id),
  computation_run_id VARCHAR(36) REFERENCES computation_runs(id), tool_run_id VARCHAR(100),
  evidence_ids JSON NOT NULL, data_time TIMESTAMP, content_hash VARCHAR(64) NOT NULL,
  verification_status VARCHAR(32) NOT NULL, freshness_status VARCHAR(32) NOT NULL, metadata_json JSON NOT NULL,
  created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL, deleted_at TIMESTAMP,
  CONSTRAINT uq_writing_document_block UNIQUE (document_id, block_id)
);
CREATE TABLE IF NOT EXISTS writing_fact_conflicts (
  id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  project_id VARCHAR(36) NOT NULL REFERENCES writing_projects(id), fact_key VARCHAR(160) NOT NULL,
  candidate_fact_ids JSON NOT NULL, conflict_type VARCHAR(64) NOT NULL, status VARCHAR(32) NOT NULL,
  resolution JSON NOT NULL, resolved_by VARCHAR(36) REFERENCES users(id), resolved_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL, deleted_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS writing_decision_gates (
  id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  project_id VARCHAR(36) NOT NULL REFERENCES writing_projects(id), gate_key VARCHAR(100) NOT NULL,
  name VARCHAR(200) NOT NULL, required BOOLEAN NOT NULL, status VARCHAR(32) NOT NULL, current_record_id VARCHAR(36),
  created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL, deleted_at TIMESTAMP,
  CONSTRAINT uq_writing_decision_gate UNIQUE (project_id, gate_key)
);
CREATE TABLE IF NOT EXISTS writing_decision_records (
  id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  project_id VARCHAR(36) NOT NULL REFERENCES writing_projects(id),
  gate_id VARCHAR(36) NOT NULL REFERENCES writing_decision_gates(id), decision VARCHAR(64) NOT NULL,
  original_value JSON NOT NULL, new_value JSON NOT NULL, reason TEXT NOT NULL,
  decided_by VARCHAR(36) NOT NULL REFERENCES users(id), created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL, deleted_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS writing_alternative_plans (
  id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  project_id VARCHAR(36) NOT NULL REFERENCES writing_projects(id), plan_key VARCHAR(100) NOT NULL,
  name VARCHAR(200) NOT NULL, version INTEGER NOT NULL, objective VARCHAR(64) NOT NULL, weights JSON NOT NULL,
  inputs JSON NOT NULL, constraints JSON NOT NULL, result JSON NOT NULL, algorithm JSON NOT NULL,
  unresolved_gaps JSON NOT NULL, risks JSON NOT NULL, status VARCHAR(32) NOT NULL,
  selected_by VARCHAR(36) REFERENCES users(id), selected_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL, deleted_at TIMESTAMP,
  CONSTRAINT uq_writing_plan_version UNIQUE (project_id, plan_key, version)
);
CREATE TABLE IF NOT EXISTS writing_review_issues (
  id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  project_id VARCHAR(36) NOT NULL REFERENCES writing_projects(id),
  document_id VARCHAR(36) NOT NULL REFERENCES writing_documents(id), block_id VARCHAR(100),
  issue_type VARCHAR(64) NOT NULL, severity VARCHAR(32) NOT NULL, message TEXT NOT NULL,
  evidence JSON NOT NULL, status VARCHAR(32) NOT NULL, resolved_by VARCHAR(36) REFERENCES users(id), resolved_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL, deleted_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS writing_agent_sessions (
  id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  project_id VARCHAR(36) NOT NULL REFERENCES writing_projects(id), document_id VARCHAR(36) REFERENCES writing_documents(id),
  user_id VARCHAR(36) NOT NULL REFERENCES users(id), harness_session_id VARCHAR(200) NOT NULL, status VARCHAR(32) NOT NULL,
  created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL, deleted_at TIMESTAMP,
  CONSTRAINT uq_writing_harness_session UNIQUE (project_id, harness_session_id)
);
CREATE TABLE IF NOT EXISTS writing_event_projections (
  id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  project_id VARCHAR(36) NOT NULL REFERENCES writing_projects(id),
  session_id VARCHAR(36) NOT NULL REFERENCES writing_agent_sessions(id), sequence BIGINT NOT NULL,
  event_type VARCHAR(100) NOT NULL, payload JSON NOT NULL, occurred_at TIMESTAMP NOT NULL,
  created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL, deleted_at TIMESTAMP,
  CONSTRAINT uq_writing_event_sequence UNIQUE (session_id, sequence)
);
CREATE TABLE IF NOT EXISTS writing_export_templates (
  id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id), code VARCHAR(100) NOT NULL,
  name VARCHAR(200) NOT NULL, current_version_id VARCHAR(36), enabled BOOLEAN NOT NULL,
  created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL, deleted_at TIMESTAMP,
  CONSTRAINT uq_writing_export_template_code UNIQUE (tenant_id, code)
);
CREATE TABLE IF NOT EXISTS writing_export_template_versions (
  id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  template_id VARCHAR(36) NOT NULL REFERENCES writing_export_templates(id), version INTEGER NOT NULL,
  format_config JSON NOT NULL, object_key VARCHAR(500), checksum VARCHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL, created_by VARCHAR(36) NOT NULL REFERENCES users(id),
  created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL, deleted_at TIMESTAMP,
  CONSTRAINT uq_writing_export_template_version UNIQUE (template_id, version)
);
CREATE TABLE IF NOT EXISTS writing_export_jobs (
  id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  project_id VARCHAR(36) NOT NULL REFERENCES writing_projects(id),
  document_id VARCHAR(36) NOT NULL REFERENCES writing_documents(id),
  document_version_id VARCHAR(36) NOT NULL REFERENCES writing_document_versions(id),
  template_version_id VARCHAR(36) REFERENCES writing_export_template_versions(id),
  requested_by VARCHAR(36) NOT NULL REFERENCES users(id), output_format VARCHAR(32) NOT NULL,
  status VARCHAR(32) NOT NULL, progress INTEGER NOT NULL, object_key VARCHAR(500), checksum VARCHAR(64), manifest JSON NOT NULL,
  error_code VARCHAR(100), error_message TEXT, started_at TIMESTAMP, finished_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL, deleted_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_writing_projects_tenant_status ON writing_projects(tenant_id, status, updated_at);
CREATE INDEX IF NOT EXISTS ix_writing_facts_project_state ON writing_project_facts(project_id, active, freshness_status);
CREATE INDEX IF NOT EXISTS ix_writing_bindings_document_state ON writing_block_bindings(document_id, freshness_status);
CREATE INDEX IF NOT EXISTS ix_computation_runs_project_recent ON computation_runs(project_id, created_at);
CREATE INDEX IF NOT EXISTS ix_writing_plans_project_state ON writing_alternative_plans(project_id, status, version);
CREATE INDEX IF NOT EXISTS ix_writing_review_open ON writing_review_issues(document_id, status, severity);
CREATE INDEX IF NOT EXISTS ix_writing_events_session_sequence ON writing_event_projections(session_id, sequence);
CREATE INDEX IF NOT EXISTS ix_writing_exports_document_recent ON writing_export_jobs(document_id, created_at);
