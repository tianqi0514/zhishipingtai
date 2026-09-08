CREATE TABLE IF NOT EXISTS writing_comments (
    id VARCHAR(36) PRIMARY KEY,
    tenant_id VARCHAR(36) NOT NULL REFERENCES tenants(id),
    project_id VARCHAR(36) NOT NULL REFERENCES writing_projects(id),
    document_id VARCHAR(36) NOT NULL REFERENCES writing_documents(id),
    thread_id VARCHAR(36) NOT NULL,
    parent_id VARCHAR(36) NULL REFERENCES writing_comments(id),
    block_id VARCHAR(100) NULL,
    content TEXT NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'open',
    created_by VARCHAR(36) NOT NULL REFERENCES users(id),
    resolved_by VARCHAR(36) NULL REFERENCES users(id),
    resolved_at TIMESTAMP NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP NULL
);

CREATE INDEX IF NOT EXISTS ix_writing_comments_tenant_id ON writing_comments(tenant_id);
CREATE INDEX IF NOT EXISTS ix_writing_comments_project_id ON writing_comments(project_id);
CREATE INDEX IF NOT EXISTS ix_writing_comments_document_id ON writing_comments(document_id);
CREATE INDEX IF NOT EXISTS ix_writing_comments_thread_id ON writing_comments(thread_id);
CREATE INDEX IF NOT EXISTS ix_writing_comments_parent_id ON writing_comments(parent_id);
CREATE INDEX IF NOT EXISTS ix_writing_comments_block_id ON writing_comments(block_id);
CREATE INDEX IF NOT EXISTS ix_writing_comments_status ON writing_comments(status);
CREATE INDEX IF NOT EXISTS ix_writing_comments_created_by ON writing_comments(created_by);
