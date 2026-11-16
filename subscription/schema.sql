PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS subscribers (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('pending','active','expired','unsubscribed')),
    preferences TEXT NOT NULL DEFAULT '[]',
    trial_remaining INTEGER NOT NULL DEFAULT 3 CHECK (trial_remaining BETWEEN 0 AND 3),
    token_nonce TEXT NOT NULL,
    confirmation_expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    confirmed_at TEXT,
    unsubscribed_at TEXT,
    suppressed_at TEXT
);

CREATE TABLE IF NOT EXISTS orders (
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

CREATE TABLE IF NOT EXISTS subscriptions (
    subscriber_id TEXT PRIMARY KEY REFERENCES subscribers(id),
    status TEXT NOT NULL CHECK (status IN ('active','expired','grace_period')),
    expires_at TEXT NOT NULL,
    expiry_order_id TEXT NOT NULL REFERENCES orders(id),
    updated_at TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS subscription_expiry_insert
BEFORE INSERT ON subscriptions
WHEN NOT EXISTS (
    SELECT 1 FROM orders WHERE id = NEW.expiry_order_id
    AND subscriber_id = NEW.subscriber_id AND status = 'paid'
    AND applied_expires_at = NEW.expires_at
)
BEGIN SELECT RAISE(ABORT, 'expiry requires a confirmed order'); END;

CREATE TRIGGER IF NOT EXISTS subscription_expiry_update
BEFORE UPDATE OF expires_at, expiry_order_id ON subscriptions
WHEN (NEW.expires_at != OLD.expires_at OR NEW.expiry_order_id != OLD.expiry_order_id)
AND NOT EXISTS (
    SELECT 1 FROM orders WHERE id = NEW.expiry_order_id
    AND subscriber_id = NEW.subscriber_id AND status = 'paid'
    AND applied_expires_at = NEW.expires_at AND id != OLD.expiry_order_id
)
BEGIN SELECT RAISE(ABORT, 'expiry requires a new confirmed order'); END;

CREATE TABLE IF NOT EXISTS daily_issue (
    id TEXT PRIMARY KEY,
    issue_date TEXT NOT NULL,
    edition TEXT NOT NULL CHECK (edition IN ('morning','evening','legacy')),
    sections TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    UNIQUE(issue_date, edition)
);

CREATE TRIGGER IF NOT EXISTS daily_issue_immutable_update
BEFORE UPDATE ON daily_issue
BEGIN SELECT RAISE(ABORT, 'published issues are immutable'); END;

CREATE TRIGGER IF NOT EXISTS daily_issue_immutable_delete
BEFORE DELETE ON daily_issue
BEGIN SELECT RAISE(ABORT, 'published issues are immutable'); END;

CREATE TABLE IF NOT EXISTS delivery_log (
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
CREATE INDEX IF NOT EXISTS delivery_due ON delivery_log(status, next_attempt_at);

CREATE TABLE IF NOT EXISTS renewal_reminders (
    subscriber_id TEXT NOT NULL REFERENCES subscribers(id),
    expires_at TEXT NOT NULL,
    reminder_type TEXT NOT NULL CHECK (reminder_type IN ('t7','t3','t1','expired','paused','trial_end')),
    sent_at TEXT NOT NULL,
    UNIQUE(subscriber_id, expires_at, reminder_type)
);

-- Kept after an account is deleted. email_hmac cannot be reversed to an address; it only stops
-- deletion from resetting the free trial or from lifting a suppression.
CREATE TABLE IF NOT EXISTS deleted_accounts (
    email_hmac TEXT PRIMARY KEY,
    trial_remaining INTEGER NOT NULL CHECK (trial_remaining BETWEEN 0 AND 3),
    suppressed INTEGER NOT NULL DEFAULT 0,
    deleted_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS request_limits (
    bucket TEXT PRIMARY KEY,
    window_start TEXT NOT NULL,
    count INTEGER NOT NULL
);
PRAGMA user_version = 3;
