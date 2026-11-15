import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from subscription.store import Store


NOW = datetime(2026, 11, 15, 11, tzinfo=timezone.utc)


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "brief.db")

    def subscriber(self, email="reader@example.com"):
        account = self.store.register(email, ["markets"], NOW)
        self.store.confirm(account["confirmation_token"], NOW)
        return account

    def pay(self, account, trade="trade-1", now=NOW):
        order = self.store.create_order(account["id"], "monthly", now)
        self.store.confirm_payment(order["order_token"], trade, 990, now)
        return order
