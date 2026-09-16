-- dialect: postgresql
CREATE TABLE IF NOT EXISTS writing_public_references (
  id VARCHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  project_id VARCHAR(36) NOT NULL REFERENCES writing_projects(id),
  title VARCHAR(500) NOT NULL,
  publisher VARCHAR(300) NOT NULL,
  url VARCHAR(2000) NOT NULL,
  publication_date VARCHAR(32),
  retrieved_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  excerpt TEXT NOT NULL,
  applicable_scope JSONB NOT NULL DEFAULT '{}'::jsonb,
  validity_status VARCHAR(32) NOT NULL DEFAULT 'current',
  usage_sections JSONB NOT NULL DEFAULT '[]'::jsonb,
  checksum VARCHAR(64) NOT NULL,
  created_by VARCHAR(36) NOT NULL REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMPTZ,
  CONSTRAINT uq_writing_public_reference UNIQUE (project_id, url, checksum)
);
CREATE INDEX IF NOT EXISTS ix_writing_public_reference_tenant ON writing_public_references(tenant_id);
CREATE INDEX IF NOT EXISTS ix_writing_public_reference_project ON writing_public_references(project_id);
CREATE INDEX IF NOT EXISTS ix_writing_public_reference_validity ON writing_public_references(validity_status);
CREATE INDEX IF NOT EXISTS ix_writing_public_reference_checksum ON writing_public_references(checksum);
