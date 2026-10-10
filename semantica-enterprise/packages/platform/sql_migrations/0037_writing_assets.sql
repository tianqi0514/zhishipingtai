CREATE TABLE IF NOT EXISTS writing_asset_versions (
 id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
 space_id VARCHAR(36) NOT NULL REFERENCES knowledge_spaces(id),
 extraction_run_id VARCHAR(36) NOT NULL REFERENCES writing_extraction_runs(id),
 asset_key VARCHAR(100) NOT NULL, kind VARCHAR(50) NOT NULL, version INTEGER NOT NULL,
 title VARCHAR(300) NOT NULL, definition JSON NOT NULL, checksum VARCHAR(64) NOT NULL,
 status VARCHAR(32) NOT NULL DEFAULT 'candidate', validation JSON NOT NULL,
 reviewed_by VARCHAR(36) REFERENCES users(id), reviewed_at TIMESTAMP,
 created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
 deleted_at TIMESTAMP, CONSTRAINT uq_writing_asset_version UNIQUE (tenant_id, space_id, asset_key, version)
);
CREATE INDEX IF NOT EXISTS ix_writing_asset_scope ON writing_asset_versions(tenant_id, space_id, kind, status);
CREATE TABLE IF NOT EXISTS writing_asset_releases (
 id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
 space_id VARCHAR(36) NOT NULL REFERENCES knowledge_spaces(id), release_number INTEGER NOT NULL,
 snapshot JSON NOT NULL, checksum VARCHAR(64) NOT NULL, published_by VARCHAR(36) NOT NULL REFERENCES users(id),
 created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
 deleted_at TIMESTAMP, CONSTRAINT uq_writing_asset_release UNIQUE (space_id, release_number)
);
CREATE INDEX IF NOT EXISTS ix_writing_asset_release_scope ON writing_asset_releases(tenant_id, space_id);
