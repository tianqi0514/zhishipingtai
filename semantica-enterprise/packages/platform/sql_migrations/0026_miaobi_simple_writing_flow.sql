CREATE TABLE IF NOT EXISTS writing_generation_runs (
  id VARCHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  project_id VARCHAR(36) NOT NULL REFERENCES writing_projects(id),
  document_id VARCHAR(36) NOT NULL REFERENCES writing_documents(id),
  agent_session_id VARCHAR(36) REFERENCES writing_agent_sessions(id),
  requested_by VARCHAR(36) NOT NULL REFERENCES users(id),
  status VARCHAR(32) NOT NULL,
  stage VARCHAR(64) NOT NULL,
  progress INTEGER NOT NULL,
  input_snapshot JSON NOT NULL,
  toolbox_result JSON NOT NULL,
  section_plan JSON NOT NULL,
  assistant_message_id VARCHAR(36) REFERENCES conversation_messages(id),
  quality_report JSON NOT NULL,
  error_code VARCHAR(100),
  error_message TEXT,
  started_at TIMESTAMP,
  finished_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  deleted_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS writing_input_changes (
  id VARCHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  project_id VARCHAR(36) NOT NULL REFERENCES writing_projects(id),
  document_id VARCHAR(36) NOT NULL REFERENCES writing_documents(id),
  requested_by VARCHAR(36) NOT NULL REFERENCES users(id),
  status VARCHAR(32) NOT NULL,
  changes JSON NOT NULL,
  impact JSON NOT NULL,
  preview_fingerprint VARCHAR(64) NOT NULL,
  applied_document_version_id VARCHAR(36) REFERENCES writing_document_versions(id),
  applied_by VARCHAR(36) REFERENCES users(id),
  applied_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  deleted_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS writing_agent_edits (
  id VARCHAR(36) PRIMARY KEY,
  tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
  project_id VARCHAR(36) NOT NULL REFERENCES writing_projects(id),
  document_id VARCHAR(36) NOT NULL REFERENCES writing_documents(id),
  document_version_id VARCHAR(36) NOT NULL REFERENCES writing_document_versions(id),
  requested_by VARCHAR(36) NOT NULL REFERENCES users(id),
  action VARCHAR(64) NOT NULL,
  block_id VARCHAR(100),
  instruction TEXT NOT NULL,
  original_text TEXT NOT NULL,
  suggested_text TEXT NOT NULL,
  status VARCHAR(32) NOT NULL,
  agent_session_id VARCHAR(36) REFERENCES writing_agent_sessions(id),
  assistant_message_id VARCHAR(36) REFERENCES conversation_messages(id),
  decided_by VARCHAR(36) REFERENCES users(id),
  decided_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  deleted_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_writing_generation_project_recent
  ON writing_generation_runs(project_id, created_at);
CREATE INDEX IF NOT EXISTS ix_writing_generation_document_state
  ON writing_generation_runs(document_id, status, updated_at);
CREATE INDEX IF NOT EXISTS ix_writing_input_change_project_state
  ON writing_input_changes(project_id, status, created_at);
CREATE INDEX IF NOT EXISTS ix_writing_agent_edit_document_state
  ON writing_agent_edits(document_id, status, created_at);
