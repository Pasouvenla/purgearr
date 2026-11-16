"""
The connection test tells you whether a deletion will actually be possible.

Both media servers refuse deletion with an auth-looking error when the feature
is disabled in their settings, which reads like a bad token. Reading the
permission during the connection test surfaces it there, instead of halfway
through a purge of dozens of items.
"""
import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.routes.settings as settings_route
from app.database import Base


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


class FakeRequest:
    """Only what the route touches: the submitted form."""

    def __init__(self, form):
        self._form = form

    async def form(self):
        return self._form


class FakeServer:
    def __init__(self, reachable=True, deletion_allowed=True):
        self.reachable = reachable
        self.deletion_allowed = deletion_allowed

    def test_connection(self):
        return self.reachable

    def allows_media_deletion(self):
        return self.deletion_allowed


def badge(db, monkeypatch, service, server):
    monkeypatch.setattr(settings_route, "require_auth",
                        lambda request, db: SimpleNamespace(id=1))
    module = "app.connectors.plex" if service == "plex" else "app.connectors.jellyfin"
    classe = "PlexConnector" if service == "plex" else "JellyfinConnector"
    import importlib
    monkeypatch.setattr(importlib.import_module(module), classe, lambda *a, **k: server)
    form = {f"{service}_url": "http://server:32400", f"{service}_token": "tok"}
    response = asyncio.run(
        settings_route.test_connection.__wrapped__(service, FakeRequest(form), db)
    )
    return response.body.decode()


@pytest.mark.parametrize("service", ["plex", "jellyfin"])
def test_a_server_that_refuses_deletions_is_flagged_amber(db, monkeypatch, service):
    html = badge(db, monkeypatch, service, FakeServer(deletion_allowed=False))
    assert "media deletion disabled" in html
    assert "amber" in html


@pytest.mark.parametrize("service", ["plex", "jellyfin"])
def test_a_ready_server_is_green(db, monkeypatch, service):
    html = badge(db, monkeypatch, service, FakeServer())
    assert "Connected" in html and "amber" not in html


@pytest.mark.parametrize("service", ["plex", "jellyfin"])
def test_an_unreachable_server_still_fails(db, monkeypatch, service):
    html = badge(db, monkeypatch, service, FakeServer(reachable=False))
    assert "Failed" in html


@pytest.mark.parametrize("service", ["plex", "jellyfin"])
def test_an_unreadable_permission_is_not_treated_as_a_refusal(db, monkeypatch, service):
    """allows_media_deletion returns None when the preference cannot be read."""
    html = badge(db, monkeypatch, service, FakeServer(deletion_allowed=None))
    assert "Connected" in html and "amber" not in html
