import base64
import logging

import requests

logger = logging.getLogger(__name__)


def _encode_header(value: str) -> str:
    """Make a header value safe for ntfy.

    HTTP header values must be latin-1 (RFC 7230); requests/http.client raise
    UnicodeEncodeError on characters outside it (em dash U+2014, emoji, smart
    quotes…). ntfy decodes RFC 2047 encoded-words, so non-ASCII values are wrapped
    as =?UTF-8?B?...?=. ASCII values are returned verbatim.
    """
    if value.isascii():
        return value
    encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
    return f"=?UTF-8?B?{encoded}?="

NTFY_PRIORITY_MAP = {
    "low": "low",
    "default": "default",
    "high": "high",
    "urgent": "urgent",
}

GOTIFY_PRIORITY_MAP = {
    "low": 2,
    "default": 5,
    "high": 8,
    "urgent": 10,
}

DISCORD_COLOR = 0xE5A00D


class Notifier:
    """Supports ntfy, Gotify, Discord, and Apprise notification services."""

    def __init__(self, notifier_type: str, url: str, token: str = ""):
        self.notifier_type = notifier_type  # "ntfy", "gotify", "discord", "apprise", or "none"
        self.url = url.rstrip("/") if url else ""
        self.token = token or ""

    def send(self, title: str, message: str, priority: str = "default") -> bool:
        if self.notifier_type == "none" or not self.url:
            return True

        try:
            if self.notifier_type == "ntfy":
                return self._send_ntfy(title, message, priority)
            elif self.notifier_type == "gotify":
                return self._send_gotify(title, message, priority)
            elif self.notifier_type == "discord":
                return self._send_discord(title, message)
            elif self.notifier_type == "apprise":
                return self._send_apprise(title, message)
            else:
                logger.warning(f"Unknown notifier type: {self.notifier_type}")
                return False
        except Exception as e:
            logger.error(f"Notifier send failed: {e}")
            return False

    def _send_ntfy(self, title: str, message: str, priority: str) -> bool:
        headers = {
            "Title": _encode_header(title),
            "Priority": NTFY_PRIORITY_MAP.get(priority, "default"),
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            r = requests.post(
                self.url,
                data=message.encode("utf-8"),
                headers=headers,
                timeout=10,
            )
            r.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"ntfy send failed: {e}")
            return False

    def _send_gotify(self, title: str, message: str, priority: str) -> bool:
        params = {}
        if self.token:
            params["token"] = self.token
        try:
            r = requests.post(
                f"{self.url}/message",
                json={
                    "title": title,
                    "message": message,
                    "priority": GOTIFY_PRIORITY_MAP.get(priority, 5),
                },
                params=params,
                timeout=10,
            )
            r.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Gotify send failed: {e}")
            return False

    def _send_discord(self, title: str, message: str) -> bool:
        try:
            r = requests.post(
                self.url,
                json={"embeds": [{"title": title, "description": message, "color": DISCORD_COLOR}]},
                timeout=10,
            )
            r.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Discord send failed: {e}")
            return False

    def _send_apprise(self, title: str, message: str) -> bool:
        """POST to Apprise API server at {url}/notify."""
        try:
            r = requests.post(
                f"{self.url}/notify",
                json={"title": title, "body": message},
                timeout=10,
            )
            r.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Apprise send failed: {e}")
            return False
