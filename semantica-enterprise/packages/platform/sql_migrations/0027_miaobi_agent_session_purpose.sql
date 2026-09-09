-- dialect:postgresql
ALTER TABLE writing_agent_sessions
  ADD COLUMN IF NOT EXISTS purpose VARCHAR(32) NOT NULL DEFAULT 'editing'
;

UPDATE writing_agent_sessions AS session
SET purpose = 'report_generation'
WHERE EXISTS (
  SELECT 1
  FROM writing_generation_runs AS run
  WHERE run.agent_session_id = session.id
);

CREATE INDEX IF NOT EXISTS ix_writing_agent_sessions_purpose
  ON writing_agent_sessions(purpose);
