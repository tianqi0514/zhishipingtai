CREATE INDEX IF NOT EXISTS ix_document_profiles_tenant_space ON document_profiles (tenant_id, space_id);
CREATE INDEX IF NOT EXISTS ix_document_profiles_document_generated ON document_profiles (document_id, generated_at);
CREATE UNIQUE INDEX IF NOT EXISTS ix_document_profiles_version_unique ON document_profiles (version_id);
