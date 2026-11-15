from datetime import timedelta
from unittest.mock import patch

from subscription.delivery import Delivery
from subscription.scheduler import schedule_once
from tests.support import NOW, StoreCase
from tests.test_delivery import SECTIONS


class SchedulerTest(StoreCase):
    def setUp(self):
        super().setUp()
        self.delivery = Delivery(self.store, "https://brief.example.com", "Brief <brief@example.com>")

    def test_before_noon_waits_and_late_scan_recovers_todays_frozen_issue(self):
        self.subscriber()
        with patch("subscription.scheduler.subprocess.run") as generation:
            self.assertEqual(schedule_once(self.store, self.delivery, NOW - timedelta(hours=1)), 0)
            generation.assert_not_called()
        self.store.freeze_issue("2026-11-15", SECTIONS, NOW)
        with patch("subscription.scheduler.subprocess.run") as generation:
            self.assertEqual(schedule_once(self.store, self.delivery, NOW + timedelta(hours=5)), 1)
            generation.assert_not_called()
        self.assertEqual(schedule_once(self.store, self.delivery, NOW + timedelta(hours=6)), 0)

    def test_missing_snapshot_runs_existing_generator_then_enqueues(self):
        self.subscriber()
        def generate(*args, **kwargs):
            self.store.freeze_issue("2026-11-15", SECTIONS, NOW)
        with patch("subscription.scheduler.subprocess.run", side_effect=generate):
            self.assertEqual(schedule_once(self.store, self.delivery, NOW), 1)
