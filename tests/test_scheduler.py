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
        before = datetime(2026, 11, 14, 23, 59, tzinfo=timezone.utc)
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
