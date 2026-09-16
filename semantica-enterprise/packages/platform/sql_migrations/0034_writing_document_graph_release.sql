-- dialect: postgresql
ALTER TABLE writing_document_versions
  ADD COLUMN IF NOT EXISTS writing_graph_release_id VARCHAR(36) REFERENCES writing_graph_releases(id);
CREATE INDEX IF NOT EXISTS ix_writing_document_versions_graph_release
  ON writing_document_versions(writing_graph_release_id);
