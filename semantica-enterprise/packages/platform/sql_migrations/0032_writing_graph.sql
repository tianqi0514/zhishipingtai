-- dialect: postgresql
CREATE TABLE IF NOT EXISTS writing_evidence (
  id VARCHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  space_id VARCHAR(36) NOT NULL REFERENCES knowledge_spaces(id),
  document_id VARCHAR(36) NOT NULL REFERENCES documents(id),
  document_version_id VARCHAR(36) NOT NULL REFERENCES document_versions(id),
  content_element_id VARCHAR(36) REFERENCES content_elements(id),
  chunk_id VARCHAR(36) REFERENCES chunks(id),
  evidence_key VARCHAR(64) NOT NULL,
  filename VARCHAR(500) NOT NULL,
  file_version INTEGER NOT NULL,
  locator JSONB NOT NULL DEFAULT '{}'::jsonb,
  text TEXT NOT NULL,
  context_before TEXT NOT NULL DEFAULT '',
  context_after TEXT NOT NULL DEFAULT '',
  content_hash VARCHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'current',
  created_by VARCHAR(36) REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMPTZ,
  CONSTRAINT uq_writing_evidence_key UNIQUE (tenant_id, evidence_key)
);
CREATE INDEX IF NOT EXISTS ix_writing_evidence_tenant ON writing_evidence(tenant_id);
CREATE INDEX IF NOT EXISTS ix_writing_evidence_space ON writing_evidence(space_id);
CREATE INDEX IF NOT EXISTS ix_writing_evidence_document_version ON writing_evidence(document_version_id);
CREATE INDEX IF NOT EXISTS ix_writing_evidence_chunk ON writing_evidence(chunk_id);
CREATE INDEX IF NOT EXISTS ix_writing_evidence_hash ON writing_evidence(content_hash);

CREATE TABLE IF NOT EXISTS writing_extraction_runs (
  id VARCHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  space_id VARCHAR(36) NOT NULL REFERENCES knowledge_spaces(id),
  document_version_id VARCHAR(36) NOT NULL REFERENCES document_versions(id),
  model_config_id VARCHAR(36) REFERENCES model_configs(id),
  strategy_version VARCHAR(64) NOT NULL DEFAULT 'writing-graph-v1',
  prompt_schema_version VARCHAR(64) NOT NULL DEFAULT 'joint-v1',
  batch_key VARCHAR(64) NOT NULL,
  evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  status VARCHAR(32) NOT NULL DEFAULT 'queued',
  metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
  error_code VARCHAR(100),
  error_message TEXT,
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMPTZ,
  CONSTRAINT uq_writing_extraction_batch UNIQUE (document_version_id, batch_key, strategy_version)
);
CREATE INDEX IF NOT EXISTS ix_writing_extraction_tenant ON writing_extraction_runs(tenant_id);
CREATE INDEX IF NOT EXISTS ix_writing_extraction_space ON writing_extraction_runs(space_id);
CREATE INDEX IF NOT EXISTS ix_writing_extraction_version ON writing_extraction_runs(document_version_id);
CREATE INDEX IF NOT EXISTS ix_writing_extraction_status ON writing_extraction_runs(status);

CREATE TABLE IF NOT EXISTS writing_entity_candidates (
  id VARCHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  space_id VARCHAR(36) NOT NULL REFERENCES knowledge_spaces(id),
  extraction_run_id VARCHAR(36) NOT NULL REFERENCES writing_extraction_runs(id),
  candidate_key VARCHAR(64) NOT NULL,
  entity_type VARCHAR(100) NOT NULL,
  canonical_name VARCHAR(500) NOT NULL,
  normalized_name VARCHAR(500) NOT NULL,
  mention_text VARCHAR(500) NOT NULL,
  aliases JSONB NOT NULL DEFAULT '[]'::jsonb,
  evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
  normalization_status VARCHAR(32) NOT NULL DEFAULT 'candidate',
  verification_status VARCHAR(32) NOT NULL DEFAULT 'candidate',
  canonical_entity_id VARCHAR(36) REFERENCES canonical_entities(id),
  candidate_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMPTZ,
  CONSTRAINT uq_writing_entity_candidate UNIQUE (extraction_run_id, candidate_key)
);
CREATE INDEX IF NOT EXISTS ix_writing_entity_candidate_space ON writing_entity_candidates(space_id);
CREATE INDEX IF NOT EXISTS ix_writing_entity_candidate_status ON writing_entity_candidates(verification_status);
CREATE INDEX IF NOT EXISTS ix_writing_entity_candidate_normalized ON writing_entity_candidates(normalized_name);

CREATE TABLE IF NOT EXISTS writing_claims (
  id VARCHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  space_id VARCHAR(36) NOT NULL REFERENCES knowledge_spaces(id),
  extraction_run_id VARCHAR(36) NOT NULL REFERENCES writing_extraction_runs(id),
  document_version_id VARCHAR(36) NOT NULL REFERENCES document_versions(id),
  claim_key VARCHAR(64) NOT NULL,
  subject JSONB NOT NULL DEFAULT '{}'::jsonb,
  predicate VARCHAR(300) NOT NULL,
  object_value JSONB NOT NULL DEFAULT '{}'::jsonb,
  claim_type VARCHAR(64) NOT NULL DEFAULT 'assertion',
  time_scope JSONB NOT NULL DEFAULT '{}'::jsonb,
  applicable_scope JSONB NOT NULL DEFAULT '{}'::jsonb,
  evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
  conflict_status VARCHAR(32) NOT NULL DEFAULT 'clear',
  verification_status VARCHAR(32) NOT NULL DEFAULT 'candidate',
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMPTZ,
  CONSTRAINT uq_writing_claim UNIQUE (extraction_run_id, claim_key)
);
CREATE INDEX IF NOT EXISTS ix_writing_claim_space ON writing_claims(space_id);
CREATE INDEX IF NOT EXISTS ix_writing_claim_status ON writing_claims(verification_status);
CREATE INDEX IF NOT EXISTS ix_writing_claim_predicate ON writing_claims(predicate);

CREATE TABLE IF NOT EXISTS writing_facts (
  id VARCHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  space_id VARCHAR(36) NOT NULL REFERENCES knowledge_spaces(id),
  fact_key VARCHAR(160) NOT NULL,
  subject JSONB NOT NULL DEFAULT '{}'::jsonb,
  subject_entity_id VARCHAR(36) REFERENCES canonical_entities(id),
  subject_candidate_id VARCHAR(36) REFERENCES writing_entity_candidates(id),
  predicate VARCHAR(300) NOT NULL,
  object_value JSONB NOT NULL DEFAULT '{}'::jsonb,
  object_entity_id VARCHAR(36) REFERENCES canonical_entities(id),
  object_candidate_id VARCHAR(36) REFERENCES writing_entity_candidates(id),
  value_type VARCHAR(64) NOT NULL DEFAULT 'string',
  unit VARCHAR(64),
  time_scope JSONB NOT NULL DEFAULT '{}'::jsonb,
  applicable_scope JSONB NOT NULL DEFAULT '{}'::jsonb,
  claim_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  origin_type VARCHAR(32) NOT NULL DEFAULT 'claim',
  verification_status VARCHAR(32) NOT NULL DEFAULT 'candidate',
  verified_by VARCHAR(36) REFERENCES users(id),
  verified_at TIMESTAMPTZ,
  valid_from TIMESTAMPTZ,
  valid_to TIMESTAMPTZ,
  version INTEGER NOT NULL DEFAULT 1,
  superseded_by VARCHAR(36) REFERENCES writing_facts(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMPTZ,
  CONSTRAINT uq_writing_fact_version UNIQUE (space_id, fact_key, version)
);
CREATE INDEX IF NOT EXISTS ix_writing_fact_tenant ON writing_facts(tenant_id);
CREATE INDEX IF NOT EXISTS ix_writing_fact_space ON writing_facts(space_id);
CREATE INDEX IF NOT EXISTS ix_writing_fact_key ON writing_facts(fact_key);
CREATE INDEX IF NOT EXISTS ix_writing_fact_status ON writing_facts(verification_status);
CREATE INDEX IF NOT EXISTS ix_writing_fact_predicate ON writing_facts(predicate);

CREATE TABLE IF NOT EXISTS writing_relations (
  id VARCHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  space_id VARCHAR(36) NOT NULL REFERENCES knowledge_spaces(id),
  subject_entity_id VARCHAR(36) REFERENCES canonical_entities(id),
  subject_candidate_id VARCHAR(36) REFERENCES writing_entity_candidates(id),
  predicate VARCHAR(300) NOT NULL,
  object_entity_id VARCHAR(36) REFERENCES canonical_entities(id),
  object_candidate_id VARCHAR(36) REFERENCES writing_entity_candidates(id),
  fact_id VARCHAR(36) NOT NULL REFERENCES writing_facts(id),
  claim_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  evidence_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  verification_status VARCHAR(32) NOT NULL DEFAULT 'candidate',
  valid_from TIMESTAMPTZ,
  valid_to TIMESTAMPTZ,
  version INTEGER NOT NULL DEFAULT 1,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMPTZ,
  CONSTRAINT uq_writing_relation_fact_version UNIQUE (fact_id, version)
);
CREATE INDEX IF NOT EXISTS ix_writing_relation_space ON writing_relations(space_id);
CREATE INDEX IF NOT EXISTS ix_writing_relation_subject ON writing_relations(subject_entity_id);
CREATE INDEX IF NOT EXISTS ix_writing_relation_subject_candidate ON writing_relations(subject_candidate_id);
CREATE INDEX IF NOT EXISTS ix_writing_relation_object ON writing_relations(object_entity_id);
CREATE INDEX IF NOT EXISTS ix_writing_relation_object_candidate ON writing_relations(object_candidate_id);
CREATE INDEX IF NOT EXISTS ix_writing_relation_predicate ON writing_relations(predicate);

CREATE TABLE IF NOT EXISTS writing_governance_actions (
  id VARCHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  space_id VARCHAR(36) NOT NULL REFERENCES knowledge_spaces(id),
  target_type VARCHAR(32) NOT NULL,
  target_id VARCHAR(36) NOT NULL,
  action VARCHAR(32) NOT NULL,
  before_value JSONB NOT NULL DEFAULT '{}'::jsonb,
  after_value JSONB NOT NULL DEFAULT '{}'::jsonb,
  reason TEXT NOT NULL DEFAULT '',
  impact JSONB NOT NULL DEFAULT '{}'::jsonb,
  actor_id VARCHAR(36) NOT NULL REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_writing_governance_space ON writing_governance_actions(space_id);
CREATE INDEX IF NOT EXISTS ix_writing_governance_target ON writing_governance_actions(target_type, target_id);

CREATE TABLE IF NOT EXISTS writing_graph_releases (
  id VARCHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  space_id VARCHAR(36) NOT NULL REFERENCES knowledge_spaces(id),
  release_number INTEGER NOT NULL,
  graph_name VARCHAR(200) NOT NULL,
  evidence_count INTEGER NOT NULL DEFAULT 0,
  entity_count INTEGER NOT NULL DEFAULT 0,
  claim_count INTEGER NOT NULL DEFAULT 0,
  fact_count INTEGER NOT NULL DEFAULT 0,
  relation_count INTEGER NOT NULL DEFAULT 0,
  checksum VARCHAR(64) NOT NULL,
  validation_report JSONB NOT NULL DEFAULT '{}'::jsonb,
  status VARCHAR(32) NOT NULL DEFAULT 'published',
  created_by VARCHAR(36) NOT NULL REFERENCES users(id),
  published_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMPTZ,
  CONSTRAINT uq_writing_graph_release_number UNIQUE (space_id, release_number)
);
CREATE INDEX IF NOT EXISTS ix_writing_graph_release_space ON writing_graph_releases(space_id);
CREATE INDEX IF NOT EXISTS ix_writing_graph_release_status ON writing_graph_releases(status);

CREATE TABLE IF NOT EXISTS writing_graph_release_items (
  id VARCHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  release_id VARCHAR(36) NOT NULL REFERENCES writing_graph_releases(id),
  object_type VARCHAR(32) NOT NULL,
  object_id VARCHAR(36) NOT NULL,
  object_version INTEGER NOT NULL DEFAULT 1,
  content_hash VARCHAR(64) NOT NULL,
  snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMPTZ,
  CONSTRAINT uq_writing_graph_release_item UNIQUE (release_id, object_type, object_id, object_version)
);
CREATE INDEX IF NOT EXISTS ix_writing_graph_release_item_release ON writing_graph_release_items(release_id);
CREATE INDEX IF NOT EXISTS ix_writing_graph_release_item_object ON writing_graph_release_items(object_type, object_id);

ALTER TABLE writing_projects ADD COLUMN IF NOT EXISTS knowledge_space_id VARCHAR(36) REFERENCES knowledge_spaces(id);
ALTER TABLE writing_projects ADD COLUMN IF NOT EXISTS writing_graph_release_id VARCHAR(36) REFERENCES writing_graph_releases(id);
ALTER TABLE writing_documents ADD COLUMN IF NOT EXISTS writing_graph_release_id VARCHAR(36) REFERENCES writing_graph_releases(id);
CREATE INDEX IF NOT EXISTS ix_writing_projects_knowledge_space ON writing_projects(knowledge_space_id);
CREATE INDEX IF NOT EXISTS ix_writing_projects_graph_release ON writing_projects(writing_graph_release_id);
CREATE INDEX IF NOT EXISTS ix_writing_documents_graph_release ON writing_documents(writing_graph_release_id);

CREATE TABLE IF NOT EXISTS writing_sections (
  id VARCHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  project_id VARCHAR(36) NOT NULL REFERENCES writing_projects(id),
  document_id VARCHAR(36) NOT NULL REFERENCES writing_documents(id),
  section_key VARCHAR(100) NOT NULL,
  parent_id VARCHAR(36) REFERENCES writing_sections(id),
  title VARCHAR(500) NOT NULL,
  instruction TEXT NOT NULL DEFAULT '',
  ordinal INTEGER NOT NULL DEFAULT 0,
  readiness_status VARCHAR(32) NOT NULL DEFAULT 'ready',
  generation_status VARCHAR(32) NOT NULL DEFAULT 'not_started',
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMPTZ,
  CONSTRAINT uq_writing_document_section UNIQUE (document_id, section_key)
);
CREATE INDEX IF NOT EXISTS ix_writing_section_document ON writing_sections(document_id);
CREATE INDEX IF NOT EXISTS ix_writing_section_project ON writing_sections(project_id);

CREATE TABLE IF NOT EXISTS writing_chunks (
  id VARCHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  project_id VARCHAR(36) NOT NULL REFERENCES writing_projects(id),
  document_id VARCHAR(36) NOT NULL REFERENCES writing_documents(id),
  document_version_id VARCHAR(36) NOT NULL REFERENCES writing_document_versions(id),
  section_id VARCHAR(36) REFERENCES writing_sections(id),
  chunk_id VARCHAR(100) NOT NULL,
  block_type VARCHAR(64) NOT NULL,
  content JSONB NOT NULL DEFAULT '{}'::jsonb,
  content_hash VARCHAR(64) NOT NULL,
  verification_status VARCHAR(32) NOT NULL DEFAULT 'unverified',
  freshness_status VARCHAR(32) NOT NULL DEFAULT 'current',
  manual_override BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMPTZ,
  CONSTRAINT uq_writing_version_chunk UNIQUE (document_version_id, chunk_id)
);
CREATE INDEX IF NOT EXISTS ix_writing_chunk_document ON writing_chunks(document_id);
CREATE INDEX IF NOT EXISTS ix_writing_chunk_version ON writing_chunks(document_version_id);
CREATE INDEX IF NOT EXISTS ix_writing_chunk_project ON writing_chunks(project_id);
CREATE INDEX IF NOT EXISTS ix_writing_chunk_chunk_id ON writing_chunks(chunk_id);

CREATE TABLE IF NOT EXISTS writing_chunk_dependencies (
  id VARCHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  project_id VARCHAR(36) NOT NULL REFERENCES writing_projects(id),
  document_id VARCHAR(36) NOT NULL REFERENCES writing_documents(id),
  writing_chunk_id VARCHAR(36) NOT NULL REFERENCES writing_chunks(id),
  binding_type VARCHAR(64) NOT NULL,
  binding_id VARCHAR(100) NOT NULL,
  binding_version VARCHAR(100),
  binding_hash VARCHAR(64) NOT NULL,
  verification_status VARCHAR(32) NOT NULL DEFAULT 'unverified',
  freshness_status VARCHAR(32) NOT NULL DEFAULT 'current',
  dependency_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMPTZ,
  CONSTRAINT uq_writing_chunk_dependency UNIQUE (writing_chunk_id, binding_type, binding_id)
);
CREATE INDEX IF NOT EXISTS ix_writing_chunk_dependency_chunk ON writing_chunk_dependencies(writing_chunk_id);
CREATE INDEX IF NOT EXISTS ix_writing_chunk_dependency_binding ON writing_chunk_dependencies(binding_type, binding_id);
CREATE INDEX IF NOT EXISTS ix_writing_chunk_dependency_document ON writing_chunk_dependencies(document_id);
