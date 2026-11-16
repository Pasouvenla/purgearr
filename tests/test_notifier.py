import base64
from unittest.mock import MagicMock, patch

from app.connectors.notifier import Notifier, _encode_header


def test_encode_header_ascii_passthrough():
    assert _encode_header("Purgearr Test") == "Purgearr Test"


def test_encode_header_non_ascii_is_rfc2047_and_latin1_safe():
    # Em dash U+2014 — the exact character from issue #1
    out = _encode_header("Purgearr — Test")
    assert out.startswith("=?UTF-8?B?") and out.endswith("?=")
    # Round-trips back to the original title
    payload = out[len("=?UTF-8?B?"):-len("?=")]
    assert base64.b64decode(payload).decode("utf-8") == "Purgearr — Test"
    # The whole header value is now safe for HTTP (latin-1) — the original bug
    out.encode("latin-1")


@patch("app.connectors.notifier.requests.post")
def test_ntfy_unicode_title_does_not_crash(mock_post):
    mock_post.return_value = MagicMock(raise_for_status=lambda: None)
    n = Notifier("ntfy", "https://ntfy.example.com/purgearr")

    assert n.send("Purgearr — Test", "Test notification from Purgearr settings.") is True

    headers = mock_post.call_args.kwargs["headers"]
    headers["Title"].encode("latin-1")  # must not raise UnicodeEncodeError
    # Body stays raw UTF-8
    assert mock_post.call_args.kwargs["data"] == "Test notification from Purgearr settings.".encode("utf-8")


@patch("app.connectors.notifier.requests.post")
def test_ntfy_ascii_title_sent_verbatim(mock_post):
    mock_post.return_value = MagicMock(raise_for_status=lambda: None)
    n = Notifier("ntfy", "https://ntfy.example.com/purgearr")

    assert n.send("Purgearr Test", "body") is True
    assert mock_post.call_args.kwargs["headers"]["Title"] == "Purgearr Test"
