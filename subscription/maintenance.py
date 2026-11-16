"""Routine upkeep: backup rotation, log trimming and a health check for an external monitor."""

import re
from datetime import datetime, timedelta, timezone

from .store import BEIJING, EDITIONS, stamp

BACKUP_NAME = re.compile(r"^brief-(\d{8}-\d{6})\.db(?:\.key)?$")


def prune_backups(directory, keep_days, now):
    """Delete backup files older than keep_days. Names carry their own UTC timestamp, so copies and
    restores that change file times cannot keep old data alive. Returns the deleted file names."""
    removed = []
    if keep_days <= 0:
        return removed
    cutoff = now - timedelta(days=keep_days)
    for path in sorted(directory.iterdir()):
        match = BACKUP_NAME.match(path.name)
        if not match or not path.is_file():
            continue
        made = datetime.strptime(match.group(1), "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
        if made < cutoff:
            path.unlink()
            removed.append(path.name)
    return removed


def prune_logs(store, days, now):
    """Drop stored mail bodies once they have no further use. Rows stay, because their business key is
    what prevents an issue from being queued twice. Failed and needs_review rows are kept for a person."""
    cutoff = stamp(now - timedelta(days=days))
    with store.transaction() as conn:
        blanked = conn.execute("""UPDATE delivery_log SET payload='{}' WHERE status IN ('success','skipped')
                                  AND created_at<? AND payload!='{}'""", (cutoff,)).rowcount
        limits = conn.execute("DELETE FROM request_limits WHERE window_start<?", (stamp(now - timedelta(days=1)),)).rowcount
    with store.connect() as conn:
        # Fold the write-ahead log back into the main file so it does not grow without bound.
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return {"payloads_cleared": blanked, "rate_limit_rows_deleted": limits}


def health(store, now, lag=timedelta(minutes=15), backup_dir=None):
    """Return [(level, message)]. Any 'error' means someone should look now."""
    problems = []
    if backup_dir is not None:
        newest = None
        for path in (backup_dir.iterdir() if backup_dir.is_dir() else ()):
            match = BACKUP_NAME.match(path.name)
            if match and path.name.endswith(".db"):
                made = datetime.strptime(match.group(1), "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
                newest = max(newest, made) if newest else made
        if newest is None:
            problems.append(("error", "备份目录里没有任何备份"))
        elif now - newest > timedelta(hours=36):
            problems.append(("error", f"最近一次备份已超过 36 小时（{newest:%Y-%m-%d %H:%M} UTC），备份任务可能没有运行"))
    late = stamp(now - lag)
    with store.connect() as conn:
        waiting = conn.execute("""SELECT count(*) FROM delivery_log WHERE status IN ('pending','failed')
                                  AND attempts<6 AND next_attempt_at<?""", (late,)).fetchone()[0]
        if waiting:
            problems.append(("error", f"{waiting} 封邮件超过 15 分钟仍未发出，worker 可能没有运行"))
        stuck = conn.execute("SELECT count(*) FROM delivery_log WHERE status='sending' AND lease_until<?", (late,)).fetchone()[0]
        if stuck:
            problems.append(("error", f"{stuck} 封邮件卡在发送中，worker 可能在发送途中退出"))
        review = conn.execute("SELECT count(*) FROM delivery_log WHERE status='needs_review'").fetchone()[0]
        if review:
            problems.append(("error", f"{review} 封邮件需要人工核对服务商记录"))
        dead = conn.execute("SELECT count(*) FROM delivery_log WHERE status='failed' AND attempts>=6").fetchone()[0]
        if dead:
            problems.append(("warn", f"{dead} 封邮件重试 6 次后仍失败"))
        unmatched = conn.execute("SELECT count(*) FROM orders WHERE status='unmatched'").fetchone()[0]
        if unmatched:
            problems.append(("warn", f"{unmatched} 笔已验签的付款没有匹配订单，需要人工处理"))
        local = now.astimezone(BEIJING)
        for edition, (hour, label) in EDITIONS.items():
            release = local.replace(hour=hour, minute=0, second=0, microsecond=0)
            if local >= release + timedelta(minutes=30):
                exists = conn.execute("SELECT 1 FROM daily_issue WHERE issue_date=? AND edition=?",
                                      (local.date().isoformat(), edition)).fetchone()
                if not exists:
                    problems.append(("error", f"今天的{label}在 {hour:02d}:30 后仍没有快照，scheduler 可能没有运行或生成失败"))
    return problems
