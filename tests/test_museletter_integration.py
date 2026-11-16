"""Exercise the pinned engine's real API and sender with a fake provider."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from museletter.app import create_app
from museletter.config import Settings
from museletter.mailer import SendResult
from museletter.tokens import make_token

from newsletter import MuseletterError, Publisher
from tests.test_newsletter import CONTENT, NOW


class EngineSession:
    def __init__(self, client):
        self.client = client

    def request(self, method, url, **kwargs):
        kwargs["follow_redirects"] = kwargs.pop("allow_redirects")
        return self.client.request(method, url, **kwargs)


class MuseletterIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.mailer = AsyncMock()
        self.mailer.send_email.return_value = SendResult(message_id="fake-provider-message")
        self.app = create_app(Settings(
            api_key="integration-key", db_path=str(root / "engine.db"),
            base_url="https://news.example.com", from_email="brief@example.com",
            extra={"disable_sender": True, "mailer": self.mailer},
        ))
        self.client = self.enterContext(TestClient(self.app))
        self.auth = {"Authorization": "Bearer integration-key"}
        self.enterContext(patch.dict(os.environ, {
            "MUSELETTER_URL": "http://testserver", "MUSELETTER_API_KEY": "integration-key",
            "MUSELETTER_LIST": "default", "MUSELETTER_TAG": "",
        }))
        self.publisher = Publisher(path=root / "publisher.db", session=EngineSession(self.client))

    def add_subscriber(self, email="reader@example.com", **extra):
        response = self.client.post("/v1/lists/default/subscribers", headers=self.auth,
                                    json={"email": email, **extra})
        self.assertEqual(response.status_code, 201)
        return response.json()["id"]

    def test_signup_confirmation_delivery_and_one_click_unsubscribe(self):
        response = self.client.post("/subscribe/default", json={"email": "reader@example.com"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.mailer.send_email.call_count, 1)
        subscribers = self.client.get("/v1/lists/default/subscribers", headers=self.auth).json()["subscribers"]
        self.assertEqual(subscribers[0]["status"], "unconfirmed")
        subscriber_id = subscribers[0]["id"]
        token = make_token(self.app.state.secret, "confirm", subscriber_id)
        self.client.get("/confirm/" + token)
        self.publisher.prepare(CONTENT, NOW, "morning")
        self.publisher.publish(NOW, "morning", draft_only=True)
        with self.assertRaisesRegex(MuseletterError, "412"):
            self.publisher.publish(NOW, "morning")
        campaign_id = self.publisher.current(NOW, "morning")["campaign_id"]
        test = self.client.post(f"/v1/campaigns/{campaign_id}/test", headers=self.auth,
                                json={"to": "operator@example.com"})
        self.assertEqual(test.status_code, 200)
        self.publisher.publish(NOW, "morning")
        self.client.portal.call(self.app.state.sender.tick)
        sent = self.mailer.send_email.call_args
        self.assertIn("事实摘要", sent.args[2])
        headers = sent.kwargs["headers"]
        self.assertEqual(headers["List-Unsubscribe-Post"], "List-Unsubscribe=One-Click")
        unsubscribe_url = headers["List-Unsubscribe"].strip("<>")
        self.assertTrue(unsubscribe_url.startswith("https://news.example.com/unsubscribe/"))
        self.assertEqual(self.client.post(unsubscribe_url).status_code, 200)
        self.client.get("/confirm/" + token)
        row = self.client.get("/v1/subscribers/" + subscriber_id, headers=self.auth).json()
        self.assertEqual(row["status"], "unsubscribed")

    def test_unsubscribe_after_queueing_prevents_delivery(self):
        subscriber_id = self.add_subscriber()
        self.publisher.prepare(CONTENT, NOW, "morning")
        self.publisher.publish(NOW, "morning", skip_test=True)
        token = make_token(self.app.state.secret, "unsubscribe", subscriber_id)
        self.client.post("/unsubscribe/" + token)
        self.client.portal.call(self.app.state.sender.tick)
        self.mailer.send_email.assert_not_called()

    def test_tag_audience_excludes_other_unconfirmed_and_suppressed_readers(self):
        self.add_subscriber("vip@example.com", tags=["vip"])
        self.add_subscriber("other@example.com")
        self.add_subscriber("pending@example.com", tags=["vip"], status="unconfirmed")
        self.add_subscriber("blocked@example.com", tags=["vip"])
        self.client.post("/v1/suppressions", headers=self.auth,
                         json={"email": "blocked@example.com", "reason": "manual"})
        self.publisher.tag = "vip"
        self.publisher.prepare(CONTENT, NOW, "morning")
        self.publisher.publish(NOW, "morning", skip_test=True)
        self.client.portal.call(self.app.state.sender.tick)
        self.assertEqual(self.mailer.send_email.call_count, 1)
        self.assertEqual(self.mailer.send_email.call_args.args[0], "vip@example.com")

    def test_real_api_replays_create_key_after_a_lost_response(self):
        self.add_subscriber()
        self.publisher.prepare(CONTENT, NOW, "morning")
        payload = json.loads(self.publisher.current(NOW, "morning")["payload"])
        key = "brief-create-" + self.publisher.business_key(NOW, "morning")
        created = self.client.post("/v1/lists/default/campaigns", json=payload,
                                   headers={**self.auth, "Idempotency-Key": key}).json()
        self.publisher.publish(NOW, "morning", skip_test=True)
        campaigns = self.client.get("/v1/campaigns", headers=self.auth).json()["campaigns"]
        self.assertEqual(len(campaigns), 1)
        self.assertEqual(self.publisher.current(NOW, "morning")["campaign_id"], created["id"])
