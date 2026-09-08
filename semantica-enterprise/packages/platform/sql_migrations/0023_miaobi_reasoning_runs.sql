CREATE TABLE IF NOT EXISTS writing_reasoning_runs (
    id VARCHAR(36) PRIMARY KEY,
    tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
    project_id VARCHAR(36) NOT NULL REFERENCES writing_projects(id),
    status VARCHAR(32) NOT NULL DEFAULT 'queued',
    mode VARCHAR(32) NOT NULL DEFAULT 'preview',
    engine VARCHAR(100) NOT NULL DEFAULT 'semantica-datalog',
    engine_version VARCHAR(100) NOT NULL DEFAULT '',
    input_fact_ids JSON NOT NULL DEFAULT '[]',
    rule_manifest JSON NOT NULL DEFAULT '[]',
    result JSON NOT NULL DEFAULT '{}',
    proof JSON NOT NULL DEFAULT '{}',
    checksum VARCHAR(64) NOT NULL,
    started_at TIMESTAMPTZ NULL,
    finished_at TIMESTAMPTZ NULL,
    created_by VARCHAR(36) NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS ix_writing_reasoning_runs_tenant_id ON writing_reasoning_runs(tenant_id);
CREATE INDEX IF NOT EXISTS ix_writing_reasoning_runs_project_id ON writing_reasoning_runs(project_id);
CREATE INDEX IF NOT EXISTS ix_writing_reasoning_runs_status ON writing_reasoning_runs(status);
CREATE INDEX IF NOT EXISTS ix_writing_reasoning_runs_mode ON writing_reasoning_runs(mode);
CREATE INDEX IF NOT EXISTS ix_writing_reasoning_runs_checksum ON writing_reasoning_runs(checksum);
CREATE INDEX IF NOT EXISTS ix_writing_reasoning_runs_created_by ON writing_reasoning_runs(created_by);

-- dialect: postgresql
ALTER TABLE writing_block_bindings
    ADD COLUMN IF NOT EXISTS retrieval_query_run_id VARCHAR(36) REFERENCES query_runs(id);
CREATE INDEX IF NOT EXISTS ix_writing_block_bindings_retrieval_query_run_id
    ON writing_block_bindings(retrieval_query_run_id);
