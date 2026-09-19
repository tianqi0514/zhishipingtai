-- dialect: postgresql
CREATE TABLE IF NOT EXISTS writing_corpus_packages (
  id VARCHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  project_id VARCHAR(36) REFERENCES writing_projects(id),
  space_id VARCHAR(36) REFERENCES knowledge_spaces(id),
  code VARCHAR(100) NOT NULL,
  name VARCHAR(300) NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'draft',
  current_version_id VARCHAR(36),
  created_by VARCHAR(36) NOT NULL REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMPTZ,
  CONSTRAINT uq_writing_corpus_package_code UNIQUE (tenant_id, code)
);
CREATE INDEX IF NOT EXISTS ix_writing_corpus_package_tenant ON writing_corpus_packages(tenant_id);
CREATE INDEX IF NOT EXISTS ix_writing_corpus_package_project ON writing_corpus_packages(project_id);
CREATE INDEX IF NOT EXISTS ix_writing_corpus_package_space ON writing_corpus_packages(space_id);
CREATE INDEX IF NOT EXISTS ix_writing_corpus_package_status ON writing_corpus_packages(status);

CREATE TABLE IF NOT EXISTS writing_corpus_package_versions (
  id VARCHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  package_id VARCHAR(36) NOT NULL REFERENCES writing_corpus_packages(id),
  version INTEGER NOT NULL,
  source_document_version_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  writing_graph_release_id VARCHAR(36) REFERENCES writing_graph_releases(id),
  manifest JSONB NOT NULL DEFAULT '{}'::jsonb,
  outline JSONB NOT NULL DEFAULT '[]'::jsonb,
  skeletons JSONB NOT NULL DEFAULT '[]'::jsonb,
  style_profile JSONB NOT NULL DEFAULT '{}'::jsonb,
  artifact_mapping JSONB NOT NULL DEFAULT '{}'::jsonb,
  checksum VARCHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'ready',
  created_by VARCHAR(36) NOT NULL REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMPTZ,
  CONSTRAINT uq_writing_corpus_package_version UNIQUE (package_id, version)
);
CREATE INDEX IF NOT EXISTS ix_writing_corpus_version_tenant ON writing_corpus_package_versions(tenant_id);
CREATE INDEX IF NOT EXISTS ix_writing_corpus_version_package ON writing_corpus_package_versions(package_id);
CREATE INDEX IF NOT EXISTS ix_writing_corpus_version_graph ON writing_corpus_package_versions(writing_graph_release_id);
CREATE INDEX IF NOT EXISTS ix_writing_corpus_version_checksum ON writing_corpus_package_versions(checksum);

CREATE TABLE IF NOT EXISTS writing_inheritance_alignments (
  id VARCHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  project_id VARCHAR(36) NOT NULL REFERENCES writing_projects(id),
  corpus_package_version_id VARCHAR(36) NOT NULL REFERENCES writing_corpus_package_versions(id),
  requested_by VARCHAR(36) NOT NULL REFERENCES users(id),
  status VARCHAR(32) NOT NULL DEFAULT 'preview',
  alignment JSONB NOT NULL DEFAULT '[]'::jsonb,
  todo JSONB NOT NULL DEFAULT '[]'::jsonb,
  outline_diff JSONB NOT NULL DEFAULT '{}'::jsonb,
  blocking_issues JSONB NOT NULL DEFAULT '[]'::jsonb,
  fingerprint VARCHAR(64) NOT NULL,
  applied_by VARCHAR(36) REFERENCES users(id),
  applied_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_writing_inheritance_tenant ON writing_inheritance_alignments(tenant_id);
CREATE INDEX IF NOT EXISTS ix_writing_inheritance_project ON writing_inheritance_alignments(project_id);
CREATE INDEX IF NOT EXISTS ix_writing_inheritance_corpus ON writing_inheritance_alignments(corpus_package_version_id);
CREATE INDEX IF NOT EXISTS ix_writing_inheritance_status ON writing_inheritance_alignments(status);
CREATE INDEX IF NOT EXISTS ix_writing_inheritance_fingerprint ON writing_inheritance_alignments(fingerprint);

CREATE TABLE IF NOT EXISTS writing_change_sets (
  id VARCHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  project_id VARCHAR(36) NOT NULL REFERENCES writing_projects(id),
  document_id VARCHAR(36) NOT NULL REFERENCES writing_documents(id),
  base_document_version_id VARCHAR(36) NOT NULL REFERENCES writing_document_versions(id),
  requested_by VARCHAR(36) NOT NULL REFERENCES users(id),
  status VARCHAR(32) NOT NULL DEFAULT 'preview',
  operations JSONB NOT NULL DEFAULT '[]'::jsonb,
  semantic_verdicts JSONB NOT NULL DEFAULT '[]'::jsonb,
  propagation JSONB NOT NULL DEFAULT '{}'::jsonb,
  fingerprint VARCHAR(64) NOT NULL,
  applied_document_version_id VARCHAR(36) REFERENCES writing_document_versions(id),
  applied_by VARCHAR(36) REFERENCES users(id),
  applied_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_writing_change_set_tenant ON writing_change_sets(tenant_id);
CREATE INDEX IF NOT EXISTS ix_writing_change_set_project ON writing_change_sets(project_id);
CREATE INDEX IF NOT EXISTS ix_writing_change_set_document ON writing_change_sets(document_id);
CREATE INDEX IF NOT EXISTS ix_writing_change_set_base_version ON writing_change_sets(base_document_version_id);
CREATE INDEX IF NOT EXISTS ix_writing_change_set_status ON writing_change_sets(status);
CREATE INDEX IF NOT EXISTS ix_writing_change_set_fingerprint ON writing_change_sets(fingerprint);

CREATE TABLE IF NOT EXISTS writing_propagation_runs (
  id VARCHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  project_id VARCHAR(36) NOT NULL REFERENCES writing_projects(id),
  source_type VARCHAR(64) NOT NULL,
  source_id VARCHAR(36) NOT NULL,
  root_node_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  graph_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
  result JSONB NOT NULL DEFAULT '{}'::jsonb,
  checksum VARCHAR(64) NOT NULL,
  created_by VARCHAR(36) NOT NULL REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_writing_propagation_tenant ON writing_propagation_runs(tenant_id);
CREATE INDEX IF NOT EXISTS ix_writing_propagation_project ON writing_propagation_runs(project_id);
CREATE INDEX IF NOT EXISTS ix_writing_propagation_source_type ON writing_propagation_runs(source_type);
CREATE INDEX IF NOT EXISTS ix_writing_propagation_source_id ON writing_propagation_runs(source_id);
CREATE INDEX IF NOT EXISTS ix_writing_propagation_checksum ON writing_propagation_runs(checksum);

ALTER TABLE writing_input_changes ADD COLUMN IF NOT EXISTS rollback_document_version_id VARCHAR(36) REFERENCES writing_document_versions(id);
ALTER TABLE writing_input_changes ADD COLUMN IF NOT EXISTS rolled_back_by VARCHAR(36) REFERENCES users(id);
ALTER TABLE writing_input_changes ADD COLUMN IF NOT EXISTS rolled_back_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS ix_writing_input_change_rollback_version ON writing_input_changes(rollback_document_version_id);
