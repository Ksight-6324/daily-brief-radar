import io
import json
import os
import subprocess
import sys
import sqlite3
from contextlib import closing, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from subscription import cli
from subscription.dnscheck import check_domain, doh_lookup, domain_of
from subscription import maintenance
from subscription.maintenance import health, prune_backups, prune_logs
from tests.support import NOW, StoreCase

ROOT = Path(__file__).resolve().parents[1]


class BackupRotationTest(StoreCase):
    def make(self, directory, stamp_text, key=True):
        (directory / f"brief-{stamp_text}.db").write_text("db")
        if key:
            (directory / f"brief-{stamp_text}.db.key").write_text("key")

    def test_old_backups_and_their_keys_are_deleted_and_other_files_are_left_alone(self):
        folder = self.store.path.parent / "rotation"
        folder.mkdir()
        self.make(folder, "20261001-040000")
        self.make(folder, "20261020-040000")
        self.make(folder, "20261114-040000")
        (folder / "notes.txt").write_text("keep")
        (folder / "brief-final.db").write_text("keep")
        removed = prune_backups(folder, 30, NOW)
        self.assertEqual(sorted(removed), ["brief-20261001-040000.db", "brief-20261001-040000.db.key"])
        self.assertEqual(sorted(path.name for path in folder.iterdir()), [
            "brief-20261020-040000.db", "brief-20261020-040000.db.key",
            "brief-20261114-040000.db", "brief-20261114-040000.db.key", "brief-final.db", "notes.txt"])
        self.assertEqual(prune_backups(folder, 0, NOW), [])

    def test_backup_command_prunes_only_after_the_new_backup_is_written(self):
        folder = self.store.path.parent / "cli-backups"
        folder.mkdir()
        self.make(folder, "20200101-000000")
        env = {**os.environ, "PYTHONPATH": str(ROOT), "BRIEF_DB_PATH": str(self.store.path)}
        env.pop("BRIEF_TOKEN_SECRET", None)
        result = subprocess.run([sys.executable, "-m", "subscription.cli", "backup", "--to", str(folder), "--keep-days", "30"],
                                cwd=self.store.path.parent, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(sorted(report["removed"]), ["brief-20200101-000000.db", "brief-20200101-000000.db.key"])
        self.assertTrue(Path(report["backup"]).exists())

    def test_old_crash_residue_is_rotated_and_recent_temporary_files_are_kept(self):
        folder = self.store.path.parent / "crash-residue"
        folder.mkdir()
        old_names = [".backup-" + "a" * 32 + suffix for suffix in (".tmp", ".key")]
        for name in old_names:
            path = folder / name
            path.write_text("old")
            made = (NOW - timedelta(days=40)).timestamp()
            os.utime(path, (made, made))
        recent = folder / (".backup-" + "b" * 32 + ".tmp")
        recent.write_text("running")
        os.utime(recent, (NOW.timestamp(), NOW.timestamp()))
        self.assertEqual(prune_backups(folder, 30, NOW), sorted(old_names))
        self.assertTrue(recent.exists())

    def test_interrupted_backup_publishes_no_final_file_and_keeps_older_backups(self):
        folder = self.store.path.parent / "interrupted"
        folder.mkdir()
        self.make(folder, "20200101-000000")
        with patch.object(self.store, "connect") as source:
            source.return_value.__enter__.return_value.backup.side_effect = sqlite3.OperationalError("interrupted")
            with self.assertRaises(sqlite3.OperationalError):
                maintenance.backup_store(self.store, folder, 30, NOW)
        self.assertEqual(sorted(path.name for path in folder.iterdir()),
                         ["brief-20200101-000000.db", "brief-20200101-000000.db.key"])

    def test_key_copy_failure_does_not_publish_the_database_or_rotate_backups(self):
        folder = self.store.path.parent / "key-failure"
        folder.mkdir()
        self.make(folder, "20200101-000000")
        with patch.object(maintenance.shutil, "copyfile", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                maintenance.backup_store(self.store, folder, 30, NOW)
        self.assertEqual(sorted(path.name for path in folder.iterdir()),
                         ["brief-20200101-000000.db", "brief-20200101-000000.db.key"])


class LogTrimTest(StoreCase):
    def row(self, conn, account, key, status, created, payload='{"to":["x@example.com"],"html":"body"}'):
        conn.execute("""INSERT INTO delivery_log(id,subscriber_id,task_type,business_key,status,payload,idempotency_key,created_at,next_attempt_at)
                        VALUES(?,?,'daily_brief',?,?,?,?,?,?)""", (key, account["id"], key, status, payload, "brief/" + key, created, created))

    def test_old_bodies_are_cleared_but_rows_that_block_duplicate_sends_stay(self):
        account = self.subscriber()
        old, recent = (NOW - timedelta(days=40)).isoformat(), (NOW - timedelta(days=2)).isoformat()
        with self.store.transaction() as conn:
            self.row(conn, account, "old-success", "success", old)
            self.row(conn, account, "old-skipped", "skipped", old)
            self.row(conn, account, "old-review", "needs_review", old)
            self.row(conn, account, "recent-success", "success", recent)
            conn.execute("INSERT INTO request_limits VALUES('stale',?,1)", (old,))
            conn.execute("INSERT INTO request_limits VALUES('fresh',?,1)", (NOW.isoformat(),))
        report = prune_logs(self.store, 30, NOW)
        self.assertEqual(report, {"payloads_cleared": 2, "rate_limit_rows_deleted": 1})
        with self.store.connect() as conn:
            payloads = dict(conn.execute("SELECT id,payload FROM delivery_log"))
            self.assertEqual(conn.execute("SELECT count(*) FROM delivery_log").fetchone()[0], 4)
            self.assertEqual([row[0] for row in conn.execute("SELECT bucket FROM request_limits")], ["fresh"])
        self.assertEqual((payloads["old-success"], payloads["old-skipped"]), ("{}", "{}"))
        self.assertIn("body", payloads["old-review"])
        self.assertIn("body", payloads["recent-success"])
        self.assertEqual(prune_logs(self.store, 30, NOW)["payloads_cleared"], 0)


class HealthTest(StoreCase):
    def test_first_evening_launch_checks_the_current_edition(self):
        now = datetime(2026, 11, 16, 13, tzinfo=timezone.utc)
        self.store.freeze_issue("2026-11-16", {"ai": "source|||title|||https://e.com|||tag|||summary|||impact"}, now, edition="evening")
        self.assertEqual(health(self.store, now), [])

    def test_a_fresh_empty_or_invalid_backup_is_an_error(self):
        now = datetime(2026, 11, 15, 1, tzinfo=timezone.utc)
        self.store.freeze_issue("2026-11-15", {"ai": "source|||title|||https://e.com|||tag|||summary|||impact"}, now, edition="morning")
        folder = self.store.path.parent / "invalid"
        folder.mkdir()
        path = folder / "brief-20261115-003000.db"
        for content in (b"", b"not a database"):
            with self.subTest(content=content):
                path.write_bytes(content)
                problems = health(self.store, now, backup_dir=folder)
                self.assertTrue(any(level == "error" and "备份" in message for level, message in problems))

    def test_missing_or_changed_file_key_makes_the_backup_unhealthy(self):
        folder = self.store.path.parent / "missing-key"
        report = maintenance.backup_store(self.store, folder, 30, NOW)
        key = Path(report["backup"]).with_suffix(".db.key")
        key.unlink()
        self.assertTrue(any("令牌密钥" in message for _, message in health(self.store, NOW, backup_dir=folder)))
        key.write_text("different-key", encoding="ascii")
        self.assertTrue(any("令牌密钥" in message for _, message in health(self.store, NOW, backup_dir=folder)))

    def test_a_quiet_system_with_both_snapshots_is_healthy(self):
        morning = datetime(2026, 11, 15, 1, 0, tzinfo=timezone.utc)  # 09:00 Beijing
        self.store.freeze_issue("2026-11-15", {"ai": "来源|||t|||https://e.com|||x|||s|||i"}, morning, edition="morning")
        self.assertEqual(health(self.store, morning), [])

    def test_missing_snapshot_is_reported_only_after_the_grace_period(self):
        self.assertEqual(health(self.store, datetime(2026, 11, 15, 0, 20, tzinfo=timezone.utc)), [])  # 08:20
        problems = health(self.store, datetime(2026, 11, 15, 0, 31, tzinfo=timezone.utc))
        self.assertEqual([level for level, _ in problems], ["error"])
        self.assertIn("早报", problems[0][1])

    def test_stuck_review_failed_and_unmatched_items_are_reported(self):
        account = self.subscriber()
        now = datetime(2026, 11, 15, 0, 10, tzinfo=timezone.utc)
        old = (now - timedelta(minutes=40)).isoformat()
        with self.store.transaction() as conn:
            for key, status, attempts in (("a", "pending", 0), ("b", "needs_review", 1), ("c", "failed", 6)):
                conn.execute("""INSERT INTO delivery_log(id,subscriber_id,task_type,business_key,status,payload,idempotency_key,attempts,created_at,next_attempt_at)
                                VALUES(?,?,'daily_brief',?,?,'{}',?,?,?,?)""", (key, account["id"], key, status, "brief/" + key, attempts, old, old))
        self.store.confirm_payment("DBRunknown", "trade-x", 300, now)
        text = " ".join(f"{level}:{message}" for level, message in health(self.store, now))
        for fragment in ("error:1 封邮件超过 15 分钟", "error:1 封邮件需要人工核对", "error:1 封邮件重试 6 次", "error:1 笔已验签的付款"):
            self.assertIn(fragment, text)

    def test_backup_age_is_checked_when_a_backup_directory_is_configured(self):
        now = datetime(2026, 11, 15, 1, 0, tzinfo=timezone.utc)
        self.store.freeze_issue("2026-11-15", {"ai": "来源|||t|||https://e.com|||x|||s|||i"}, now, edition="morning")
        folder = self.store.path.parent / "age"
        self.assertIn("没有任何备份", health(self.store, now, backup_dir=folder)[0][1])
        folder.mkdir()
        (folder / "brief-20261113-000000.db").write_text("old")
        self.assertIn("超过 36 小时", health(self.store, now, backup_dir=folder)[0][1])
        with self.store.connect() as source, closing(sqlite3.connect(folder / "brief-20261115-003000.db")) as target:
            source.backup(target)
        (folder / "brief-20261115-003000.db.key").write_bytes(self.store.secret)
        self.assertEqual(health(self.store, now, backup_dir=folder), [])

    def test_check_command_exits_nonzero_on_errors(self):
        env = {**os.environ, "PYTHONPATH": str(ROOT), "BRIEF_DB_PATH": str(self.store.path)}
        env.pop("BRIEF_TOKEN_SECRET", None)
        result = subprocess.run([sys.executable, "-m", "subscription.cli", "check"], cwd=self.store.path.parent,
                                env=env, capture_output=True, text=True)
        report = json.loads(result.stdout)
        # Outside 08:30-20:00 Beijing time the result depends on the clock, so assert the contract only.
        self.assertEqual(result.returncode, 0 if report["ok"] else 1)
        self.assertEqual(set(report), {"ok", "problems"})

    def test_external_monitor_is_pinged_only_when_healthy_and_failures_hide_the_url(self):
        cases = (([], None, True), ([("error", "worker failed")], None, False),
                 ([], requests.ConnectionError("https://monitor.example/private-test-token"), False))
        for problems, failure, expected_ok in cases:
            with self.subTest(problems=problems, network_error=bool(failure)):
                output = io.StringIO()
                with patch.object(cli, "load_dotenv"), patch.object(cli, "Store", return_value=self.store), \
                     patch.object(cli, "health", return_value=list(problems)), \
                     patch.object(sys, "argv", ["brief", "check", "--ping"]), \
                     patch.dict(os.environ, {"HEALTHCHECK_PING_URL": "https://monitor.example/private-test-token"}), \
                     patch.object(cli.requests, "get", side_effect=failure) as get, redirect_stdout(output):
                    if expected_ok:
                        cli.main()
                    else:
                        with self.assertRaises(SystemExit) as exit:
                            cli.main()
                        self.assertEqual(exit.exception.code, 1)
                self.assertEqual(get.call_count, 0 if problems else 1)
                report = json.loads(output.getvalue())
                self.assertEqual(report["ok"], expected_ok)
                self.assertNotIn("private-test-token", output.getvalue())


def records(table):
    return lambda name, kind: table.get((name, kind), [])


class DnsCheckTest(StoreCase):
    def statuses(self, table, **options):
        return {item["check"]: item["status"] for item in check_domain("example.com", records(table), **options)}

    def test_txt_style_records_with_monitoring_dmarc(self):
        table = {
            ("send.example.com", "TXT"): [("TXT", "v=spf1 include:amazonses.com ~all")],
            ("send.example.com", "MX"): [("MX", "10 feedback-smtp.example.net.")],
            ("resend._domainkey.example.com", "TXT"): [("TXT", "p=MIGfMA0GCSq")],
            ("_dmarc.example.com", "TXT"): [("TXT", "v=DMARC1; p=none; rua=mailto:d@example.com")],
        }
        self.assertEqual(self.statuses(table), {"SPF": "ok", "DKIM": "ok", "DMARC": "warn"})

    def test_cname_style_records_and_enforcing_dmarc_from_the_parent_domain(self):
        table = {
            ("send.example.com", "CNAME"): [("CNAME", "x.provider.net.")],
            ("resend._domainkey.example.com", "CNAME"): [("CNAME", "k.provider.net.")],
            ("_dmarc.example.com", "TXT"): [("TXT", "v=DMARC1; p=quarantine; rua=mailto:d@example.com")],
        }
        self.assertEqual(self.statuses(table), {"SPF": "ok", "DKIM": "ok", "DMARC": "ok"})
        sub = check_domain("mail.example.com", records({
            ("send.mail.example.com", "CNAME"): [("CNAME", "x.")], ("resend._domainkey.mail.example.com", "CNAME"): [("CNAME", "k.")],
            ("_dmarc.example.com", "TXT"): [("TXT", "v=DMARC1; p=reject")]}))
        self.assertEqual({item["check"]: item["status"] for item in sub}["DMARC"], "ok")

    def test_missing_records_fail_and_root_only_spf_warns(self):
        self.assertEqual(self.statuses({}), {"SPF": "fail", "DKIM": "fail", "DMARC": "fail"})
        table = {("example.com", "TXT"): [("TXT", "v=spf1 -all")]}
        self.assertEqual(self.statuses(table)["SPF"], "warn")

    def test_sender_domain_is_read_from_the_from_address(self):
        self.assertEqual(domain_of("Brief <Brief@News.Example.com>"), "news.example.com")
        with self.assertRaises(ValueError):
            domain_of("not-an-address")

    def test_doh_answers_are_parsed_and_long_txt_strings_joined(self):
        response = Mock()
        response.raise_for_status = Mock()
        response.json.return_value = {"Answer": [{"type": 16, "data": '"v=DMARC1; " "p=reject"'}, {"type": 5, "data": "k.provider.net."}, {"type": 1, "data": "1.2.3.4"}]}
        with patch("subscription.dnscheck.requests.get", return_value=response) as get:
            answers = doh_lookup("_dmarc.example.com", "TXT")
        self.assertEqual(answers, [("TXT", "v=DMARC1; p=reject"), ("CNAME", "k.provider.net.")])
        self.assertEqual(get.call_args.kwargs["params"], {"name": "_dmarc.example.com", "type": "TXT"})
