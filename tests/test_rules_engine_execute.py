"""
Integration tests for rules_engine.execute_rule().

evaluate_rule() decides *which* items match; these tests cover what happens to
them afterwards. The regression they guard is the same one the manual delete
route had: an item the arr tools do not own used to be dropped from the
library and counted as deleted, so the rule "succeeded" every run while the
arr tool kept the series and re-downloaded it.
"""
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.services.rules_engine as engine
from app.database import Base
from app.models.action_log import ActionLog
from app.models.media import MediaItem
from app.models.rule import Rule


@pytest.fixture
def db():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    session = sessionmaker(bind=eng)()
    yield session
    session.close()


def make_rule(db, **kwargs):
    defaults = dict(
        name="purge", enabled=True, media_type="both",
        condition_days_unwatched=0, action_delete_from_arr=False,
        action_delete_torrent=False, action_delete_files=False,
        action_unmonitor_arr=False, action_delete_from_media_server=False,
    )
    defaults.update(kwargs)
    rule = Rule(**defaults)
    db.add(rule)
    db.commit()
    return rule


def make_item(db, **kwargs):
    defaults = dict(
        source="plex", source_id="17752", media_type="series",
        title="A Lull in the Sea", watch_status="unwatched",
        file_size_bytes=2048, sonarr_id=None, radarr_id=None,
    )
    defaults.update(kwargs)
    item = MediaItem(**defaults)
    db.add(item)
    db.commit()
    return item


class FakeMediaServer:
    def __init__(self, allowed=True, result=True):
        self.allowed = allowed
        self.result = result
        self.deleted = []

    def allows_media_deletion(self):
        return self.allowed

    def delete_media(self, source_id):
        self.deleted.append(source_id)
        return None if self.result else "Plex could not remove the files (HTTP 400)"


class FakeArr:
    def __init__(self, result=True):
        self.result = result
        self.deleted = []

    def delete_series(self, arr_id, delete_files=False):
        self.deleted.append((arr_id, delete_files))
        return self.result


@pytest.fixture(autouse=True)
def _no_notifications(monkeypatch):
    """Keep the engine off the network for settings-driven side effects."""
    monkeypatch.setattr(engine, "_get_setting", lambda db, key: None)


# ---------------------------------------------------------------------------
# Unmatched items must not be silently consumed
# ---------------------------------------------------------------------------

def test_unmatched_item_is_not_counted_as_deleted(db, monkeypatch):
    rule = make_rule(db, action_delete_from_arr=True)
    make_item(db, sonarr_id=None)

    result = engine.execute_rule(rule, db)

    assert result["deleted"] == 0
    assert result["errors"]


def test_unmatched_item_stays_in_the_library(db, monkeypatch):
    rule = make_rule(db, action_delete_from_arr=True)
    make_item(db, sonarr_id=None)

    engine.execute_rule(rule, db)

    assert db.query(MediaItem).count() == 1


def test_unmatched_item_failure_is_logged(db, monkeypatch):
    rule = make_rule(db, action_delete_from_arr=True)
    make_item(db, sonarr_id=None)

    engine.execute_rule(rule, db)

    entry = db.query(ActionLog).one()
    assert entry.success is False
    # The message names the actual cause. Both routes share one explanation
    # now, and it distinguishes "not configured" from "not matched" instead of
    # lumping them together as "no arr match".
    assert "Sonarr is not configured" in entry.error_message


# ---------------------------------------------------------------------------
# Media server deletion driven by a rule
# ---------------------------------------------------------------------------

def test_rule_deletes_through_the_media_server(db, monkeypatch):
    server = FakeMediaServer()
    monkeypatch.setattr("app.routes.actions._get_media_server", lambda db: (server, "Plex"))
    rule = make_rule(db, action_delete_from_media_server=True)
    make_item(db, source_id="17752")

    result = engine.execute_rule(rule, db)

    assert server.deleted == ["17752"]
    assert result["deleted"] == 1
    assert db.query(MediaItem).count() == 0


def test_rule_reports_a_refusing_media_server(db, monkeypatch):
    server = FakeMediaServer(allowed=False)
    monkeypatch.setattr("app.routes.actions._get_media_server", lambda db: (server, "Plex"))
    rule = make_rule(db, action_delete_from_media_server=True)
    make_item(db)

    result = engine.execute_rule(rule, db)

    assert result["deleted"] == 0
    assert db.query(MediaItem).count() == 1
    assert "enable media deletion" in db.query(ActionLog).one().error_message


def test_media_server_deletion_counts_as_freed_space(db, monkeypatch):
    monkeypatch.setattr("app.routes.actions._get_media_server",
                        lambda db: (FakeMediaServer(), "Plex"))
    rule = make_rule(db, action_delete_from_media_server=True)
    make_item(db, file_size_bytes=2048)

    engine.execute_rule(rule, db)

    assert db.query(ActionLog).one().freed_bytes == 2048


# ---------------------------------------------------------------------------
# The happy arr path is unchanged
# ---------------------------------------------------------------------------

def test_matched_item_is_deleted_from_the_arr_tool(db, monkeypatch):
    sonarr = FakeArr()
    monkeypatch.setattr(engine, "_get_setting",
                        lambda db, key: {"sonarr_url": "u", "sonarr_api_key": "k"}.get(key))
    monkeypatch.setattr("app.connectors.sonarr.SonarrConnector",
                        lambda url, key: sonarr)
    rule = make_rule(db, action_delete_from_arr=True, action_delete_files=True)
    make_item(db, sonarr_id=87)

    result = engine.execute_rule(rule, db)

    assert sonarr.deleted == [(87, True)]
    assert result["deleted"] == 1


def test_dry_run_performs_no_action(db, monkeypatch):
    server = FakeMediaServer()
    monkeypatch.setattr("app.routes.actions._get_media_server", lambda db: (server, "Plex"))
    rule = make_rule(db, action_delete_from_media_server=True)
    make_item(db)

    engine.execute_rule(rule, db, dry_run=True)

    assert server.deleted == []
    assert db.query(MediaItem).count() == 1


def test_a_matched_tool_with_no_id_says_so(db, monkeypatch):
    """The other branch: Sonarr is reachable, the item just is not in it."""
    import app.routes.actions as actions
    monkeypatch.setattr(actions, "_get_media_server", lambda db: (None, "Plex"))
    monkeypatch.setattr(engine, "_get_setting",
                        lambda db, key: "x" if key in ("sonarr_url", "sonarr_api_key") else None)

    rule = make_rule(db, action_delete_from_arr=True)
    make_item(db, sonarr_id=None)

    engine.execute_rule(rule, db)

    entry = db.query(ActionLog).one()
    assert entry.success is False
    assert "not matched to any Sonarr series" in entry.error_message
