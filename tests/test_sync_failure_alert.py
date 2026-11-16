"""
Tests for the scheduled sync failure alert.

A sync that cannot reach the media server leaves the library frozen while
every other feature keeps acting on it: rules evaluate stale items and
deletions target media that may no longer exist. The scheduler used to
swallow that into a log line, so a broken connector could go unnoticed
indefinitely. These tests pin the alert that replaced the silence.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.main as main
from app.database import Base
from app.models.settings import AppSettings


@pytest.fixture
def db():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    session = sessionmaker(bind=eng)()
    yield session
    session.close()


@pytest.fixture
def sent(monkeypatch):
    """Capture what the notifier would have delivered."""
    box = []

    class FakeNotifier:
        def __init__(self, type_, url, token):
            self.type = type_

        def send(self, title, message, priority="default"):
            box.append({"title": title, "message": message, "priority": priority})
            return True

    monkeypatch.setattr("app.connectors.notifier.Notifier", FakeNotifier)
    return box


def configure_notifier(db, type_="discord"):
    db.add(AppSettings(key="notifier_type", value=type_))
    db.add(AppSettings(key="notifier_url", value="http://hook"))
    db.commit()


# ---------------------------------------------------------------------------
# _notify_sync_failure
# ---------------------------------------------------------------------------

def test_failure_is_announced_when_a_notifier_is_configured(db, sent):
    configure_notifier(db)

    main._notify_sync_failure(db, "Connection refused")

    assert len(sent) == 1
    assert "Connection refused" in sent[0]["message"]
    assert sent[0]["priority"] == "high"


def test_nothing_is_sent_when_notifications_are_off(db, sent):
    configure_notifier(db, type_="none")
    main._notify_sync_failure(db, "boom")
    assert sent == []


def test_nothing_is_sent_when_no_notifier_is_configured(db, sent):
    main._notify_sync_failure(db, "boom")
    assert sent == []


def test_a_broken_notifier_never_breaks_the_sync(db, monkeypatch):
    """Alerting is best-effort: it must not turn a sync error into a crash."""
    configure_notifier(db)

    class Exploding:
        def __init__(self, *a):
            raise RuntimeError("notifier down")

    monkeypatch.setattr("app.connectors.notifier.Notifier", Exploding)
    main._notify_sync_failure(db, "boom")  # must not raise


# ---------------------------------------------------------------------------
# _run_sync_safe
# ---------------------------------------------------------------------------

def test_errors_returned_by_the_sync_raise_an_alert(db, monkeypatch, sent):
    configure_notifier(db)
    monkeypatch.setattr(main, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    monkeypatch.setattr("app.services.sync.sync_library",
                        lambda d: {"added": 0, "errors": ["Plex lib Movies: refused"]})

    main._run_sync_safe()

    assert len(sent) == 1
    assert "refused" in sent[0]["message"]


def test_an_exception_raises_an_alert(db, monkeypatch, sent):
    configure_notifier(db)
    monkeypatch.setattr(main, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)

    def explode(d):
        raise RuntimeError("connector missing")

    monkeypatch.setattr("app.services.sync.sync_library", explode)

    main._run_sync_safe()

    assert len(sent) == 1
    assert "connector missing" in sent[0]["message"]


def test_a_clean_sync_stays_quiet(db, monkeypatch, sent):
    configure_notifier(db)
    monkeypatch.setattr(main, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    monkeypatch.setattr("app.services.sync.sync_library",
                        lambda d: {"added": 3, "updated": 1, "removed": 0, "errors": []})

    main._run_sync_safe()

    assert sent == []
