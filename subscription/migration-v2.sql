DROP TRIGGER subscription_expiry_insert;
DROP TRIGGER subscription_expiry_update;
DROP TRIGGER daily_issue_immutable_update;
DROP TRIGGER daily_issue_immutable_delete;

CREATE TABLE orders_v2 (
    id TEXT PRIMARY KEY,
    order_token TEXT NOT NULL UNIQUE,
    subscriber_id TEXT REFERENCES subscribers(id),
    plan TEXT CHECK (plan IN ('weekly','monthly','quarterly','annual')),
    amount INTEGER NOT NULL CHECK (amount > 0),
    payment_ref TEXT UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('pending','paid','refunded','unmatched')),
    created_at TEXT NOT NULL,
    paid_at TEXT,
    applied_expires_at TEXT,
    qr_code TEXT
);
INSERT INTO orders_v2 SELECT * FROM orders;
DROP TABLE orders;
ALTER TABLE orders_v2 RENAME TO orders;

CREATE TABLE daily_issue_v2 (
    id TEXT PRIMARY KEY,
    issue_date TEXT NOT NULL,
    edition TEXT NOT NULL CHECK (edition IN ('morning','evening','legacy')),
    sections TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    UNIQUE(issue_date, edition)
);
INSERT INTO daily_issue_v2(id,issue_date,edition,sections,generated_at)
    SELECT id,issue_date,'legacy',sections,generated_at FROM daily_issue;
DROP TABLE daily_issue;
ALTER TABLE daily_issue_v2 RENAME TO daily_issue;

UPDATE delivery_log SET status='skipped',lease_until=NULL,error_message='schedule replaced by morning/evening editions'
    WHERE task_type='daily_brief' AND status IN ('pending','failed');
UPDATE delivery_log SET status='needs_review',lease_until=NULL,error_message='legacy delivery outcome requires review'
    WHERE task_type='daily_brief' AND status='sending';
