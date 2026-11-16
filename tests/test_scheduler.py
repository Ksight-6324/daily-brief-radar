from datetime import datetime, timezone
from unittest.mock import patch

from subscription.delivery import Delivery
from subscription.scheduler import schedule_once
from tests.support import NOW, StoreCase
from tests.test_delivery import SECTIONS


class SchedulerTest(StoreCase):
    def setUp(self):
        super().setUp()
        self.delivery = Delivery(self.store, "https://brief.example.com", "Brief <brief@example.com>")

    def test_beijing_eight_am_and_eight_pm_have_independent_recoverable_windows(self):
        self.subscriber()
        before = datetime(2026, 11, 14, 23, 29, tzinfo=timezone.utc)
        with patch("subscription.scheduler.subprocess.run") as generation:
            self.assertEqual(schedule_once(self.store, self.delivery, before), 0)
            generation.assert_not_called()
        morning = datetime(2026, 11, 15, 0, tzinfo=timezone.utc)
        evening = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
        self.store.freeze_issue("2026-11-15", SECTIONS, morning, edition="morning")
        self.store.freeze_issue("2026-11-15", SECTIONS, evening, edition="evening")
        with patch("subscription.scheduler.subprocess.run") as generation:
            self.assertEqual(schedule_once(self.store, self.delivery, morning), 1)
            self.assertEqual(schedule_once(self.store, self.delivery, datetime(2026, 11, 15, 11, 59, tzinfo=timezone.utc)), 0)
            self.assertEqual(schedule_once(self.store, self.delivery, evening), 1)
            self.assertEqual(schedule_once(self.store, self.delivery, datetime(2026, 11, 15, 15, tzinfo=timezone.utc)), 0)
            generation.assert_not_called()
        with self.store.connect() as conn:
            self.assertEqual({row[0] for row in conn.execute("SELECT business_key FROM delivery_log")},
                             {"2026-11-15/morning", "2026-11-15/evening"})

    def test_missing_snapshot_runs_existing_generator_then_enqueues(self):
        self.subscriber()
        def generate(*args, **kwargs):
            edition = kwargs["env"]["BRIEF_EDITION"]
            self.store.freeze_issue("2026-11-15", SECTIONS, NOW, edition=edition)
        with patch("subscription.scheduler.subprocess.run", side_effect=generate):
            self.assertEqual(schedule_once(self.store, self.delivery, NOW), 1)

    def run_generator(self, now, existing=()):
        frozen = []
        def generate(*args, **kwargs):
            edition = kwargs["env"]["BRIEF_EDITION"]
            frozen.append(edition)
            self.store.freeze_issue("2026-11-15", SECTIONS, now, edition=edition)
        for edition in existing:
            self.store.freeze_issue("2026-11-15", SECTIONS, now, edition=edition)
        with patch("subscription.scheduler.subprocess.run", side_effect=generate):
            queued = schedule_once(self.store, self.delivery, now)
        return queued, frozen

    def test_morning_issue_is_frozen_before_release_and_queued_only_at_eight(self):
        self.subscriber()
        early = datetime(2026, 11, 14, 23, 30, tzinfo=timezone.utc)  # 07:30 Beijing
        queued, frozen = self.run_generator(early)
        self.assertEqual((queued, frozen), (0, ["morning"]))
        with self.store.connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM delivery_log WHERE task_type='daily_brief'").fetchone()[0], 0)
        with patch("subscription.scheduler.subprocess.run") as generation:
            self.assertEqual(schedule_once(self.store, self.delivery, datetime(2026, 11, 14, 23, 59, tzinfo=timezone.utc)), 0)
            self.assertEqual(schedule_once(self.store, self.delivery, datetime(2026, 11, 15, 0, tzinfo=timezone.utc)), 1)
            generation.assert_not_called()

    def test_evening_issue_is_frozen_at_half_past_seven_while_morning_recovery_continues(self):
        self.subscriber()
        lead = datetime(2026, 11, 15, 11, 30, tzinfo=timezone.utc)  # 19:30 Beijing
        queued, frozen = self.run_generator(lead, existing=("morning",))
        self.assertEqual(frozen, ["evening"])
        self.assertEqual(queued, 1)  # the late reader still receives the morning edition
        with self.store.connect() as conn:
            self.assertEqual([row[0] for row in conn.execute("SELECT business_key FROM delivery_log WHERE task_type='daily_brief'")],
                             ["2026-11-15/morning"])
        evening = datetime(2026, 11, 15, 12, tzinfo=timezone.utc)
        with patch("subscription.scheduler.subprocess.run") as generation:
            self.assertEqual(schedule_once(self.store, self.delivery, evening), 1)
            generation.assert_not_called()

    def test_lead_generation_failure_does_not_block_current_window_queueing(self):
        self.subscriber()
        self.store.freeze_issue("2026-11-15", SECTIONS, NOW, edition="morning")
        lead = datetime(2026, 11, 15, 11, 40, tzinfo=timezone.utc)
        with patch("subscription.scheduler.subprocess.run", side_effect=ValueError("generator down")):
            with self.assertRaises(ValueError):
                schedule_once(self.store, self.delivery, lead)
        with self.store.connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM delivery_log WHERE task_type='daily_brief'").fetchone()[0], 1)

    def test_scan_after_midnight_before_lead_does_nothing(self):
        with patch("subscription.scheduler.subprocess.run") as generation:
            self.assertEqual(schedule_once(self.store, self.delivery, datetime(2026, 11, 15, 16, 30, tzinfo=timezone.utc)), 0)
            generation.assert_not_called()
