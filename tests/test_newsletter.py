import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from newsletter import Publisher, MuseletterError
from subscription.store import BEIJING


NOW = datetime(2026, 11, 16, 0, 0, tzinfo=timezone.utc)
CONTENT = "DATE|||2026-11-16\nSLOT|||morning\nSECTION|||ai\n官方|||模型更新|||https://example.com/story|||AI|||事实摘要|||为什么重要\n"


class NewsletterTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "publisher.db"
        self.env = patch.dict(os.environ, {
            "MUSELETTER_URL": "http://127.0.0.1:8000",
            "MUSELETTER_API_KEY": "private-test-key",
            "MUSELETTER_LIST": "default", "MUSELETTER_TAG": "",
            "BRIEF_PUBLISH_DB_PATH": str(self.path),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.http = Mock()
        self.http.request.side_effect = self.response
        self.calls = []
        self.remote_status = "draft"
        self.publisher = Publisher(session=self.http)

    def response(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        result = Mock(status_code=200)
        if url.endswith("/send"):
            self.remote_status = "sending"
        result.json.return_value = {"id": "cmp_test", "status": self.remote_status}
        return result

    def test_freeze_render_and_publish_use_actual_api(self):
        self.publisher.prepare(CONTENT, NOW, "morning")
        result = self.publisher.publish(NOW, "morning")
        self.assertEqual(result["status"], "submitted")
        create, lookup, send = self.calls
        self.assertTrue(create[1].endswith("/v1/lists/default/campaigns"))
        payload = create[2]["json"]
        self.assertIn("事实摘要", payload["body_markdown"])
        self.assertNotIn("SECTION|||", payload["body_markdown"])
        self.assertNotIn("/manage", payload["body_markdown"])
        self.assertFalse(payload["track_opens"])
        self.assertEqual(create[2]["headers"]["Authorization"], "Bearer private-test-key")
        self.assertEqual(send[2]["json"], {"confirm": True, "skip_test": False})
        self.assertEqual(lookup[0], "GET")

    def test_content_is_immutable_and_repeat_publish_is_local(self):
        self.publisher.prepare(CONTENT, NOW, "morning")
        self.publisher.prepare(CONTENT.replace("事实摘要", "新内容"), NOW, "morning")
        self.publisher.publish(NOW, "morning")
        count = len(self.calls)
        self.publisher.publish(NOW, "morning")
        self.assertEqual(len(self.calls), count)
        self.assertIn("事实摘要", self.calls[0][2]["json"]["body_markdown"])

    def test_failed_send_reuses_campaign_and_frozen_payload(self):
        self.publisher.prepare(CONTENT, NOW, "morning")
        def fail_send(method, url, **kwargs):
            if url.endswith("/send"):
                raise requests.Timeout("secret reader@example.com")
            return self.response(method, url, **kwargs)
        self.http.request.side_effect = fail_send
        with self.assertRaisesRegex(MuseletterError, "request failed") as error:
            self.publisher.publish(NOW, "morning")
        self.assertNotIn("reader@", str(error.exception))
        self.http.request.side_effect = self.response
        self.publisher.publish(NOW, "morning", skip_test=True)
        creates = [call for call in self.calls if call[1].endswith("/campaigns")]
        self.assertEqual(len(creates), 1)
        self.assertTrue(self.calls[-1][2]["json"]["skip_test"])

    def test_accepted_send_with_lost_response_is_reconciled(self):
        self.publisher.prepare(CONTENT, NOW, "morning")
        def lose_response(method, url, **kwargs):
            result = self.response(method, url, **kwargs)
            if url.endswith("/send"):
                raise requests.Timeout()
            return result
        self.http.request.side_effect = lose_response
        with self.assertRaises(MuseletterError):
            self.publisher.publish(NOW, "morning")
        self.http.request.side_effect = self.response
        self.assertEqual(self.publisher.publish(NOW, "morning")["status"], "submitted")
        self.assertEqual(sum(c[1].endswith("/send") for c in self.calls), 1)

    def test_create_timeout_reuses_identical_key_and_body_after_restart(self):
        self.publisher.prepare(CONTENT, NOW, "morning")
        self.http.request.side_effect = requests.Timeout()
        with self.assertRaises(MuseletterError):
            self.publisher.publish(NOW, "morning")
        first = self.http.request.call_args.kwargs
        self.http.request.side_effect = self.response
        restarted = Publisher(session=self.http)
        restarted.publish(NOW, "morning")
        second = self.calls[0][2]
        self.assertEqual(first["headers"]["Idempotency-Key"], second["headers"]["Idempotency-Key"])
        self.assertEqual(first["json"], second["json"])

    def test_concurrent_publish_creates_and_sends_once(self):
        self.publisher.prepare(CONTENT, NOW, "morning")
        other = Publisher(session=self.http)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda pub: pub.publish(NOW, "morning"), [self.publisher, other]))
        self.assertTrue(all(r["status"] == "submitted" for r in results))
        self.assertEqual(sum(c[1].endswith("/campaigns") for c in self.calls), 1)
        self.assertEqual(sum(c[1].endswith("/send") for c in self.calls), 1)

    def test_release_window_dates_editions_and_empty_content(self):
        early = NOW.replace(hour=23, day=15, minute=30)
        self.publisher.prepare(CONTENT, early, "morning")
        self.assertEqual(self.publisher.publish(early, "morning")["status"], "scheduled")
        self.assertFalse(self.calls)
        for content in (CONTENT.replace("2026-11-16", "2026-11-15"),
                        CONTENT.replace("morning", "evening"),
                        "DATE|||2026-11-16\nSLOT|||morning\nSECTION|||unknown\nX|||Y"):
            with self.subTest(content=content), self.assertRaises(ValueError):
                self.publisher.prepare(content, NOW, "morning")

    def test_http_failures_and_redirects_hide_response_body(self):
        self.publisher.prepare(CONTENT, NOW, "morning")
        self.http.request.side_effect = None
        for status in (302, 401, 412, 500):
            self.http.request.return_value = Mock(status_code=status, text="private reader@example.com")
            with self.subTest(status=status), self.assertRaises(MuseletterError) as error:
                self.publisher.publish(NOW, "morning")
            self.assertNotIn("reader@", str(error.exception))
            self.assertFalse(self.http.request.call_args.kwargs["allow_redirects"])

    def test_audience_is_part_of_business_key_and_encoded_path(self):
        self.publisher.prepare(CONTENT, NOW, "morning")
        with patch.dict(os.environ, {"MUSELETTER_LIST": "other/list", "MUSELETTER_TAG": "vip"}):
            other = Publisher(session=self.http)
            other.prepare(CONTENT, NOW, "morning")
            other.publish(NOW, "morning")
        self.assertIn("other%2Flist/campaigns", self.calls[0][1])
        self.assertEqual(self.calls[0][2]["json"]["tag"], "vip")
        self.assertIsNone(self.publisher.current(NOW, "morning")["campaign_id"])

    def test_cli_prepare_does_not_require_provider_credentials(self):
        source = Path(self.temp.name) / "issue.md"
        today = datetime.now(timezone.utc).astimezone(BEIJING).date().isoformat()
        source.write_text(CONTENT.replace("2026-11-16", today), encoding="utf-8")
        root = Path(__file__).resolve().parents[1]
        env = {**os.environ, "MUSELETTER_API_KEY": "", "PYTHONUTF8": "1"}
        result = subprocess.run([sys.executable, "-m", "newsletter", "--edition", "morning", "prepare", "--input", str(source)],
                                cwd=root, env=env, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "prepared")

    def test_draft_can_be_created_before_release_without_sending(self):
        early = NOW.replace(hour=23, day=15, minute=30)
        self.publisher.prepare(CONTENT, early, "morning")
        result = self.publisher.publish(early, "morning", draft_only=True)
        self.assertEqual(result, {"status": "draft", "campaign_id": "cmp_test"})
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(self.publisher.current(NOW, "morning")["submitted"])

    def test_scheduler_lead_release_and_completed_windows(self):
        from newsletter import schedule_once
        with patch("newsletter.subprocess.run") as run:
            schedule_once(self.publisher, NOW.replace(hour=23, day=15, minute=29))
            run.assert_not_called()
            schedule_once(self.publisher, NOW.replace(hour=23, day=15, minute=30))
            self.assertEqual(run.call_args.kwargs["env"]["BRIEF_EDITION"], "morning")
            self.assertEqual(run.call_args.kwargs["env"]["BRIEF_MODE"], "museletter")
            self.publisher.prepare(CONTENT, NOW, "morning")
            schedule_once(self.publisher, NOW)
            self.assertEqual(run.call_count, 2)
            self.publisher.publish(NOW, "morning")
            schedule_once(self.publisher, NOW)
            self.assertEqual(run.call_count, 2)
            schedule_once(self.publisher, NOW.replace(hour=11, minute=30))
            self.assertEqual(run.call_args.kwargs["env"]["BRIEF_EDITION"], "evening")

    def test_normal_entry_reuses_snapshot_and_calls_museletter(self):
        import runpy
        self.publisher.prepare(CONTENT, NOW, "morning")
        with patch.dict(os.environ, {"BRIEF_MODE": "museletter", "BRIEF_EDITION": "morning"}), \
             patch("newsletter.utcnow", return_value=NOW), \
             patch("subscription.store.utcnow", return_value=NOW), \
             patch("dotenv.load_dotenv"), \
             patch("subprocess.run", return_value=Mock(returncode=0)) as run:
            runpy.run_path(str(Path(__file__).resolve().parents[1] / "run_all.py"))
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][1:], ["-m", "newsletter", "publish"])


if __name__ == "__main__":
    unittest.main()
