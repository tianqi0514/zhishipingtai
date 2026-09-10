CREATE TABLE IF NOT EXISTS writing_project_materials (
  id VARCHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  project_id VARCHAR(36) NOT NULL REFERENCES writing_projects(id),
  document_id VARCHAR(36) NOT NULL REFERENCES documents(id),
  version_id VARCHAR(36) NOT NULL REFERENCES document_versions(id),
  material_role VARCHAR(32) NOT NULL,
  usage_scope VARCHAR(32) NOT NULL,
  status VARCHAR(32) NOT NULL,
  material_metadata JSON NOT NULL,
  added_by VARCHAR(36) NOT NULL REFERENCES users(id),
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  deleted_at TIMESTAMP,
  CONSTRAINT uq_writing_project_material_version UNIQUE (project_id, version_id)
);
CREATE INDEX IF NOT EXISTS ix_writing_project_material_tenant ON writing_project_materials(tenant_id);
CREATE INDEX IF NOT EXISTS ix_writing_project_material_project ON writing_project_materials(project_id);
CREATE INDEX IF NOT EXISTS ix_writing_project_material_document ON writing_project_materials(document_id);
CREATE INDEX IF NOT EXISTS ix_writing_project_material_version ON writing_project_materials(version_id);
CREATE INDEX IF NOT EXISTS ix_writing_project_material_role ON writing_project_materials(material_role);
CREATE INDEX IF NOT EXISTS ix_writing_project_material_scope ON writing_project_materials(usage_scope);
CREATE INDEX IF NOT EXISTS ix_writing_project_material_status ON writing_project_materials(status);

CREATE TABLE IF NOT EXISTS ontology_suggestions (
  id VARCHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  ontology_id VARCHAR(36) NOT NULL REFERENCES ontologies(id),
  space_id VARCHAR(36) NOT NULL REFERENCES knowledge_spaces(id),
  suggestion_kind VARCHAR(32) NOT NULL,
  code VARCHAR(200) NOT NULL,
  label VARCHAR(500) NOT NULL,
  definition TEXT NOT NULL,
  payload JSON NOT NULL,
  confidence FLOAT NOT NULL,
  evidence_count INTEGER NOT NULL,
  evidence JSON NOT NULL,
  source_fingerprint VARCHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL,
  decided_by VARCHAR(36) REFERENCES users(id),
  decided_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  deleted_at TIMESTAMP,
  CONSTRAINT uq_ontology_suggestion_source UNIQUE (ontology_id, source_fingerprint, suggestion_kind, code)
);
CREATE INDEX IF NOT EXISTS ix_ontology_suggestion_tenant ON ontology_suggestions(tenant_id);
CREATE INDEX IF NOT EXISTS ix_ontology_suggestion_ontology ON ontology_suggestions(ontology_id);
CREATE INDEX IF NOT EXISTS ix_ontology_suggestion_space ON ontology_suggestions(space_id);
CREATE INDEX IF NOT EXISTS ix_ontology_suggestion_kind ON ontology_suggestions(suggestion_kind);
CREATE INDEX IF NOT EXISTS ix_ontology_suggestion_code ON ontology_suggestions(code);
CREATE INDEX IF NOT EXISTS ix_ontology_suggestion_source_fingerprint ON ontology_suggestions(source_fingerprint);
CREATE INDEX IF NOT EXISTS ix_ontology_suggestion_status ON ontology_suggestions(status);

CREATE TABLE IF NOT EXISTS ontology_versions (
  id VARCHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  ontology_id VARCHAR(36) NOT NULL REFERENCES ontologies(id),
  version INTEGER NOT NULL,
  manifest JSON NOT NULL,
  checksum VARCHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL,
  created_by VARCHAR(36) NOT NULL REFERENCES users(id),
  published_at TIMESTAMP NOT NULL,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  deleted_at TIMESTAMP,
  CONSTRAINT uq_ontology_version UNIQUE (ontology_id, version)
);
CREATE INDEX IF NOT EXISTS ix_ontology_version_tenant ON ontology_versions(tenant_id);
CREATE INDEX IF NOT EXISTS ix_ontology_version_ontology ON ontology_versions(ontology_id);
CREATE INDEX IF NOT EXISTS ix_ontology_version_checksum ON ontology_versions(checksum);
CREATE INDEX IF NOT EXISTS ix_ontology_version_status ON ontology_versions(status);
