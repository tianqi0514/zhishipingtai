-- dialect:postgresql
ALTER TABLE data_source_schema_versions DROP CONSTRAINT IF EXISTS uq_source_schema_fingerprint;
