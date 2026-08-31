CREATE INDEX IF NOT EXISTS ix_conversations_owner_recent ON conversations (tenant_id, user_id, last_message_at);
CREATE INDEX IF NOT EXISTS ix_conversation_messages_timeline ON conversation_messages (conversation_id, sequence);
CREATE INDEX IF NOT EXISTS ix_retrieval_traces_message ON retrieval_traces (message_id, created_at);
CREATE INDEX IF NOT EXISTS ix_citations_message_rank ON citations (message_id, rank);
CREATE INDEX IF NOT EXISTS ix_agent_events_timeline ON agent_event_projections (conversation_id, sequence);
CREATE INDEX IF NOT EXISTS ix_agent_credentials_expiry ON agent_credentials (expires_at, revoked_at);
