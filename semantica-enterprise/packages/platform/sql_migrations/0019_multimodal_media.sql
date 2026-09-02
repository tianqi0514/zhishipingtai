CREATE TABLE IF NOT EXISTS media_parsing_policies (
  id VARCHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  name VARCHAR(200) NOT NULL,
  description TEXT NOT NULL,
  applicable_media_types JSON NOT NULL,
  config JSON NOT NULL,
  current_version INTEGER NOT NULL,
  enabled BOOLEAN NOT NULL,
  is_default BOOLEAN NOT NULL,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  deleted_at TIMESTAMP,
  CONSTRAINT uq_media_policy_tenant_name UNIQUE (tenant_id, name)
);
CREATE INDEX IF NOT EXISTS ix_media_policy_tenant ON media_parsing_policies(tenant_id);

CREATE TABLE IF NOT EXISTS media_parsing_policy_versions (
  id VARCHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  policy_id VARCHAR(36) NOT NULL REFERENCES media_parsing_policies(id),
  version_number INTEGER NOT NULL,
  snapshot JSON NOT NULL,
  config_hash VARCHAR(64) NOT NULL,
  created_by VARCHAR(36) REFERENCES users(id),
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  deleted_at TIMESTAMP,
  CONSTRAINT uq_media_policy_version UNIQUE (policy_id, version_number),
  CONSTRAINT uq_media_policy_config_hash UNIQUE (policy_id, config_hash)
);
CREATE INDEX IF NOT EXISTS ix_media_policy_version_tenant ON media_parsing_policy_versions(tenant_id);
CREATE INDEX IF NOT EXISTS ix_media_policy_version_policy ON media_parsing_policy_versions(policy_id);

CREATE TABLE IF NOT EXISTS media_processing_runs (
  id VARCHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  space_id VARCHAR(36) NOT NULL REFERENCES knowledge_spaces(id),
  document_id VARCHAR(36) NOT NULL REFERENCES documents(id),
  version_id VARCHAR(36) NOT NULL REFERENCES document_versions(id),
  job_id VARCHAR(36) REFERENCES jobs(id),
  policy_version_id VARCHAR(36) REFERENCES media_parsing_policy_versions(id),
  policy_snapshot JSON NOT NULL,
  media_type VARCHAR(16) NOT NULL,
  status VARCHAR(32) NOT NULL,
  stage VARCHAR(64) NOT NULL,
  progress INTEGER NOT NULL,
  input_fingerprint VARCHAR(64) NOT NULL,
  cache JSON NOT NULL,
  probe JSON NOT NULL,
  asr_model_config_id VARCHAR(36) REFERENCES model_configs(id),
  vision_model_config_id VARCHAR(36) REFERENCES model_configs(id),
  warnings JSON NOT NULL,
  result JSON NOT NULL,
  error_code VARCHAR(100),
  error_message TEXT,
  cancel_requested_at TIMESTAMP,
  started_at TIMESTAMP,
  finished_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  deleted_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_media_run_version ON media_processing_runs(version_id);
CREATE INDEX IF NOT EXISTS ix_media_run_document ON media_processing_runs(document_id);
CREATE INDEX IF NOT EXISTS ix_media_run_space ON media_processing_runs(space_id);
CREATE INDEX IF NOT EXISTS ix_media_run_fingerprint ON media_processing_runs(input_fingerprint);

CREATE TABLE IF NOT EXISTS media_audio_segments (
  id VARCHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  run_id VARCHAR(36) NOT NULL REFERENCES media_processing_runs(id),
  segment_index INTEGER NOT NULL,
  time_start FLOAT NOT NULL,
  time_end FLOAT NOT NULL,
  text TEXT NOT NULL,
  language VARCHAR(32),
  speaker VARCHAR(100),
  confidence FLOAT,
  segment_metadata JSON NOT NULL,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  deleted_at TIMESTAMP,
  CONSTRAINT uq_media_audio_segment UNIQUE (run_id, segment_index)
);
CREATE INDEX IF NOT EXISTS ix_media_audio_segment_run ON media_audio_segments(run_id);

CREATE TABLE IF NOT EXISTS media_scenes (
  id VARCHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  run_id VARCHAR(36) NOT NULL REFERENCES media_processing_runs(id),
  scene_index INTEGER NOT NULL,
  time_start FLOAT NOT NULL,
  time_end FLOAT NOT NULL,
  detection_score FLOAT,
  summary TEXT NOT NULL,
  evidence JSON NOT NULL,
  status VARCHAR(32) NOT NULL,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  deleted_at TIMESTAMP,
  CONSTRAINT uq_media_scene UNIQUE (run_id, scene_index)
);
CREATE INDEX IF NOT EXISTS ix_media_scene_run ON media_scenes(run_id);

CREATE TABLE IF NOT EXISTS media_frames (
  id VARCHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  run_id VARCHAR(36) NOT NULL REFERENCES media_processing_runs(id),
  scene_id VARCHAR(36) REFERENCES media_scenes(id),
  frame_index INTEGER NOT NULL,
  timestamp_seconds FLOAT NOT NULL,
  object_key VARCHAR(1000) NOT NULL,
  thumbnail_key VARCHAR(1000) NOT NULL,
  width INTEGER,
  height INTEGER,
  sha256 VARCHAR(64) NOT NULL,
  perceptual_hash VARCHAR(32),
  selection_reason VARCHAR(64) NOT NULL,
  ocr_status VARCHAR(32) NOT NULL,
  ocr_text TEXT NOT NULL,
  ocr_confidence FLOAT,
  vision_status VARCHAR(32) NOT NULL,
  vision_result JSON NOT NULL,
  frame_metadata JSON NOT NULL,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  deleted_at TIMESTAMP,
  CONSTRAINT uq_media_frame UNIQUE (run_id, frame_index)
);
CREATE INDEX IF NOT EXISTS ix_media_frame_run ON media_frames(run_id);
CREATE INDEX IF NOT EXISTS ix_media_frame_scene ON media_frames(scene_id);
CREATE INDEX IF NOT EXISTS ix_media_frame_timestamp ON media_frames(timestamp_seconds);

-- dialect:postgresql
ALTER TABLE knowledge_spaces ADD COLUMN IF NOT EXISTS media_policy_id VARCHAR(36)
;
-- dialect:postgresql
ALTER TABLE source_connectors ADD COLUMN IF NOT EXISTS media_policy_id VARCHAR(36)
;
-- dialect:postgresql
ALTER TABLE document_versions ADD COLUMN IF NOT EXISTS media_policy_version_id VARCHAR(36)
;
-- dialect:postgresql
ALTER TABLE document_versions ADD COLUMN IF NOT EXISTS media_policy_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb
;
-- dialect:postgresql
CREATE INDEX IF NOT EXISTS ix_knowledge_spaces_media_policy_id ON knowledge_spaces(media_policy_id)
;
-- dialect:postgresql
CREATE INDEX IF NOT EXISTS ix_source_connectors_media_policy_id ON source_connectors(media_policy_id)
;
-- dialect:postgresql
CREATE INDEX IF NOT EXISTS ix_document_versions_media_policy_version_id ON document_versions(media_policy_version_id)
;
