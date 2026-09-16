-- dialect: postgresql
ALTER TABLE index_releases ALTER COLUMN opensearch_index DROP NOT NULL;
ALTER TABLE index_releases ALTER COLUMN qdrant_collection DROP NOT NULL;
ALTER TABLE index_releases ALTER COLUMN model_config_id DROP NOT NULL;
ALTER TABLE index_releases ALTER COLUMN embedding_dimension DROP NOT NULL;
