import hashlib
import hmac
import json
import logging
import time

import requests

logger = logging.getLogger(__name__)


class WebhookSender:
    """Sends signed outgoing webhook POSTs for Purgearr events."""

    def __init__(self, url: str, secret: str = ""):
        self.url = url.rstrip("/") if url else ""
        self.secret = secret or ""

    def send(self, event: str, payload: dict) -> bool:
        if not self.url:
            return True
        body = json.dumps({"event": event, "timestamp": int(time.time()), **payload})
        headers = {
            "Content-Type": "application/json",
            "X-Purgearr-Event": event,
        }
        if self.secret:
            sig = hmac.new(self.secret.encode(), body.encode(), hashlib.sha256).hexdigest()
            headers["X-Purgearr-Signature"] = f"sha256={sig}"
        try:
            r = requests.post(self.url, data=body, headers=headers, timeout=10)
            r.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Webhook send failed [{event}]: {e}")
            return False
