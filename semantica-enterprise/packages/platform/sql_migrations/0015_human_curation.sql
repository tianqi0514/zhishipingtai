CREATE INDEX IF NOT EXISTS ix_curation_batches_space_recent ON curation_batches (space_id, created_at);
CREATE INDEX IF NOT EXISTS ix_curation_decisions_target ON curation_decisions (space_id, target_type, target_id, field_path, created_at);
CREATE INDEX IF NOT EXISTS ix_curation_decisions_version_status ON curation_decisions (version_id, status, created_at);
CREATE INDEX IF NOT EXISTS ix_curation_overlays_effective ON curation_overlays (space_id, target_type, target_id, status);
CREATE INDEX IF NOT EXISTS ix_curation_cases_queue ON curation_cases (space_id, status, severity, created_at);
CREATE INDEX IF NOT EXISTS ix_knowledge_releases_active ON knowledge_releases (space_id, status, release_number);
