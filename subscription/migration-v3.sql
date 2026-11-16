-- v3 adds the 'login_link' task type. SQLite cannot alter a CHECK constraint, so rebuild the outbox.
CREATE TABLE delivery_log_v3 (
    id TEXT PRIMARY KEY,
    subscriber_id TEXT NOT NULL REFERENCES subscribers(id),
    task_type TEXT NOT NULL CHECK (task_type IN ('daily_brief','renewal_reminder','confirmation','login_link')),
    business_key TEXT NOT NULL,
    issue_id TEXT REFERENCES daily_issue(id),
    status TEXT NOT NULL CHECK (status IN ('pending','sending','success','failed','skipped','needs_review')),
    payload TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    first_attempt_at TEXT,
    next_attempt_at TEXT NOT NULL,
    lease_until TEXT,
    sent_at TEXT,
    provider_id TEXT,
    error_message TEXT,
    UNIQUE(subscriber_id, task_type, business_key)
);
INSERT INTO delivery_log_v3(id,subscriber_id,task_type,business_key,issue_id,status,payload,idempotency_key,attempts,created_at,first_attempt_at,next_attempt_at,lease_until,sent_at,provider_id,error_message)
    SELECT id,subscriber_id,task_type,business_key,issue_id,status,payload,idempotency_key,attempts,created_at,first_attempt_at,next_attempt_at,lease_until,sent_at,provider_id,error_message FROM delivery_log;
DROP TABLE delivery_log;
ALTER TABLE delivery_log_v3 RENAME TO delivery_log;
