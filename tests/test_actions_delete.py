"""
Integration tests for the bulk delete endpoint.

Regression guard for the worst failure mode this app can have: reporting a
deletion as successful while silently doing nothing. An item whose arr id was
never resolved used to take no branch at all, leaving the error list empty —
so it was logged as deleted and dropped from the library while Sonarr/Radarr
still held it and kept re-downloading it.

The route is called directly (it is a plain function) against an in-memory
database, with the connector factories stubbed out.
"""
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.routes.actions as actions
from app.database import Base
from app.models.action_log import ActionLog
from app.models.media import MediaItem


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def _authenticated(monkeypatch):
    monkeypatch.setattr(actions, "require_auth", lambda request, db: SimpleNamespace(id=1))


class FakeArr:
    """Records deletion calls and reports whatever `result` says."""

    def __init__(self, result=True, download_ids=None):
        self.result = result
        self.deleted = []
        self.download_ids = download_ids or []

    def delete_series(self, arr_id, delete_files=False):
        self.deleted.append((arr_id, delete_files))
        return self.result

    delete_movie = delete_series

    def get_download_ids(self, arr_id):
        """Mirrors the real connector: the history is where infohashes live."""
        return self.download_ids


def add_item(db, **kwargs):
    defaults = dict(
        source="plex", source_id="1", media_type="series",
        title="A Lull in the Sea", sonarr_id=None, radarr_id=None,
        file_size_bytes=1024,
    )
    defaults.update(kwargs)
    item = MediaItem(**defaults)
    db.add(item)
    db.commit()
    return item


def call_delete(db, item_ids, **flags):
    body = actions.DeleteRequest(item_ids=item_ids, **flags)
    response = actions.bulk_delete(body, request=None, db=db)
    return json.loads(response.body)["results"]


def wire(monkeypatch, sonarr=None, radarr=None, seerr=None, media_server=None, server_name="Plex"):
    monkeypatch.setattr(actions, "_get_sonarr", lambda db: sonarr)
    monkeypatch.setattr(actions, "_get_radarr", lambda db: radarr)
    monkeypatch.setattr(actions, "_get_seerr", lambda db: seerr)
    monkeypatch.setattr(actions, "_get_media_server", lambda db: (media_server, server_name))


# ---------------------------------------------------------------------------
# The silent-success regression
# ---------------------------------------------------------------------------

def test_unmatched_series_is_reported_as_a_failure(db, monkeypatch):
    wire(monkeypatch, sonarr=FakeArr())
    item = add_item(db, sonarr_id=None)

    results = call_delete(db, [item.id])

    assert results[0]["success"] is False
    assert "not matched to any Sonarr series" in results[0]["errors"][0]


def test_unmatched_item_is_kept_in_the_library(db, monkeypatch):
    """Dropping the row would hide the problem and orphan the arr entry."""
    wire(monkeypatch, sonarr=FakeArr())
    item = add_item(db, sonarr_id=None)

    call_delete(db, [item.id])

    assert db.query(MediaItem).count() == 1


def test_unmatched_movie_names_radarr_in_the_error(db, monkeypatch):
    wire(monkeypatch, radarr=FakeArr())
    item = add_item(db, media_type="movie", title="Wuthering Heights", radarr_id=None)

    results = call_delete(db, [item.id])

    assert results[0]["success"] is False
    assert "not matched to any Radarr movie" in results[0]["errors"][0]


def test_missing_connector_is_reported_rather_than_skipped(db, monkeypatch):
    wire(monkeypatch, sonarr=None)
    item = add_item(db, sonarr_id=42)

    results = call_delete(db, [item.id])

    assert results[0]["success"] is False
    assert "Sonarr is not configured" in results[0]["errors"][0]


def test_failure_is_recorded_in_the_action_log(db, monkeypatch):
    wire(monkeypatch, sonarr=FakeArr())
    item = add_item(db, sonarr_id=None)

    call_delete(db, [item.id])

    entry = db.query(ActionLog).one()
    assert entry.success is False
    assert entry.error_message


# ---------------------------------------------------------------------------
# The happy path must keep working
# ---------------------------------------------------------------------------

def test_matched_series_is_deleted_from_sonarr(db, monkeypatch):
    sonarr = FakeArr()
    wire(monkeypatch, sonarr=sonarr)
    item = add_item(db, sonarr_id=87)

    results = call_delete(db, [item.id], delete_files=True)

    assert results[0]["success"] is True
    assert sonarr.deleted == [(87, True)]
    assert db.query(MediaItem).count() == 0


def test_arr_refusal_is_propagated(db, monkeypatch):
    wire(monkeypatch, sonarr=FakeArr(result=False))
    item = add_item(db, sonarr_id=87)

    results = call_delete(db, [item.id])

    assert results[0]["success"] is False
    assert "Sonarr deletion failed" in results[0]["errors"]
    assert db.query(MediaItem).count() == 1


def test_unknown_item_id_is_reported(db, monkeypatch):
    wire(monkeypatch)
    results = call_delete(db, [4242])
    assert results[0]["success"] is False
    assert results[0]["errors"] == ["Not found"]


# ---------------------------------------------------------------------------
# Seerr
# ---------------------------------------------------------------------------

class FakeSeerr:
    def __init__(self, request=None, delete_ok=True):
        self.request = request
        self.delete_ok = delete_ok
        self.deleted = []

    def find_request_for_media(self, tmdb_id, media_type):
        return self.request

    def delete_request(self, request_id):
        self.deleted.append(request_id)
        return self.delete_ok


def test_seerr_cleanup_without_tmdb_id_is_reported(db, monkeypatch):
    """Previously skipped in silence, so the request survived the purge."""
    wire(monkeypatch, sonarr=FakeArr(), seerr=FakeSeerr())
    item = add_item(db, sonarr_id=87, tmdb_id=None)

    results = call_delete(db, [item.id], delete_seerr_request=True)

    assert results[0]["success"] is False
    assert "no TMDB id" in results[0]["errors"][0]


def test_seerr_request_is_deleted_when_resolvable(db, monkeypatch):
    seerr = FakeSeerr(request={"id": 611})
    wire(monkeypatch, sonarr=FakeArr(), seerr=seerr)
    item = add_item(db, sonarr_id=87, tmdb_id=209867)

    results = call_delete(db, [item.id], delete_seerr_request=True)

    assert results[0]["success"] is True
    assert results[0]["seerr_deleted"] is True
    assert seerr.deleted == [611]


def test_absent_seerr_request_is_not_an_error(db, monkeypatch):
    """Not every library item was requested through Seerr."""
    wire(monkeypatch, sonarr=FakeArr(), seerr=FakeSeerr(request=None))
    item = add_item(db, sonarr_id=87, tmdb_id=209867)

    results = call_delete(db, [item.id], delete_seerr_request=True)

    assert results[0]["success"] is True
    assert results[0]["seerr_deleted"] is False


# ---------------------------------------------------------------------------
# Media server deletion
# ---------------------------------------------------------------------------

class FakeMediaServer:
    """Stands in for Plex or Jellyfin; `allowed` mirrors the server setting."""

    def __init__(self, allowed=True, result=True):
        self.allowed = allowed
        self.result = result
        self.deleted = []

    def allows_media_deletion(self):
        return self.allowed

    def delete_media(self, source_id):
        self.deleted.append(source_id)
        return None if self.result else "Plex could not remove the files (HTTP 400)"

    delete_item = delete_media


def test_media_server_deletion_targets_the_source_id(db, monkeypatch):
    server = FakeMediaServer()
    wire(monkeypatch, media_server=server)
    item = add_item(db, source_id="17752", sonarr_id=None)

    results = call_delete(db, [item.id], delete_from_arr=False,
                          delete_from_media_server=True)

    assert results[0]["success"] is True
    assert server.deleted == ["17752"]


def test_media_server_deletion_rescues_an_unmatched_item(db, monkeypatch):
    """The point of the feature: no arr owns it, so this is the only route."""
    server = FakeMediaServer()
    wire(monkeypatch, sonarr=FakeArr(), media_server=server)
    item = add_item(db, source_id="42", sonarr_id=None)

    results = call_delete(db, [item.id], delete_from_arr=False,
                          delete_from_media_server=True)

    assert results[0]["success"] is True
    assert db.query(MediaItem).count() == 0


def test_disabled_media_deletion_is_explained(db, monkeypatch):
    """Plex answers a bare 401 in this case, which reads like a bad token."""
    wire(monkeypatch, media_server=FakeMediaServer(allowed=False))
    item = add_item(db, sonarr_id=None)

    results = call_delete(db, [item.id], delete_from_arr=False,
                          delete_from_media_server=True)

    assert results[0]["success"] is False
    assert "enable media deletion in its settings" in results[0]["errors"][0]


def test_unconfigured_media_server_is_reported(db, monkeypatch):
    wire(monkeypatch, media_server=None, server_name="Jellyfin")
    item = add_item(db, sonarr_id=None)

    results = call_delete(db, [item.id], delete_from_arr=False,
                          delete_from_media_server=True)

    assert results[0]["success"] is False
    assert "Jellyfin is not configured" in results[0]["errors"][0]


def test_jellyfin_uses_its_own_delete_method(db, monkeypatch):
    server = FakeMediaServer()
    wire(monkeypatch, media_server=server, server_name="Jellyfin")
    item = add_item(db, source_id="abc123", sonarr_id=None)

    call_delete(db, [item.id], delete_from_arr=False, delete_from_media_server=True)

    assert server.deleted == ["abc123"]


def test_media_server_refusal_is_propagated(db, monkeypatch):
    wire(monkeypatch, media_server=FakeMediaServer(result=False))
    item = add_item(db, sonarr_id=None)

    results = call_delete(db, [item.id], delete_from_arr=False,
                          delete_from_media_server=True)

    assert results[0]["success"] is False
    assert "could not remove the files" in results[0]["errors"][0]
    assert db.query(MediaItem).count() == 1


def test_media_server_deletion_counts_towards_freed_space(db, monkeypatch):
    wire(monkeypatch, media_server=FakeMediaServer())
    item = add_item(db, sonarr_id=None, file_size_bytes=4096)

    call_delete(db, [item.id], delete_from_arr=False, delete_from_media_server=True)

    assert db.query(ActionLog).one().freed_bytes == 4096


def test_unknown_deletion_permission_still_attempts(db, monkeypatch):
    """A pref that cannot be read must not block the user from trying."""
    server = FakeMediaServer(allowed=None)
    wire(monkeypatch, media_server=server)
    item = add_item(db, sonarr_id=None)

    results = call_delete(db, [item.id], delete_from_arr=False,
                          delete_from_media_server=True)

    assert results[0]["success"] is True
    assert server.deleted


# ---------------------------------------------------------------------------
# Telling Plex to forget the files the arr tool just removed
# ---------------------------------------------------------------------------

class RescanningServer:
    """A media server that records the rescan, and can refuse the deletion."""

    def __init__(self, delete_error=None):
        self.delete_error = delete_error
        self.deleted = []
        self.rescans = 0

    def delete_media(self, source_id):
        self.deleted.append(source_id)
        return self.delete_error

    def forget_missing_files(self):
        self.rescans += 1
        return []


def test_plex_is_asked_to_rescan_after_the_arr_removed_the_files(db, monkeypatch):
    """The arr tool deletes the files; Plex keeps its entry until it looks again."""
    server = RescanningServer()
    wire(monkeypatch, sonarr=FakeArr(), media_server=server)
    item = add_item(db, sonarr_id=10)

    call_delete(db, [item.id], delete_files=True, delete_from_media_server=False)

    assert server.rescans == 1
    assert server.deleted == []          # no DELETE: that is what fails without write access


def test_the_rescan_happens_once_for_the_whole_batch(db, monkeypatch):
    server = RescanningServer()
    wire(monkeypatch, sonarr=FakeArr(), media_server=server)
    ids = [add_item(db, sonarr_id=10 + n, source_id=str(n)).id for n in range(3)]

    call_delete(db, ids, delete_files=True, delete_from_media_server=False)

    assert server.rescans == 1


def test_no_rescan_when_nothing_was_deleted(db, monkeypatch):
    """Every item failed: there is nothing for Plex to notice."""
    server = RescanningServer()
    wire(monkeypatch, sonarr=FakeArr(result=False), media_server=server)
    item = add_item(db, sonarr_id=10)

    results = call_delete(db, [item.id], delete_files=True)

    assert results[0]["success"] is False
    assert server.rescans == 0


def test_no_rescan_when_the_files_were_left_alone(db, monkeypatch):
    """Removing the arr entry without its files leaves Plex nothing to forget."""
    server = RescanningServer()
    wire(monkeypatch, sonarr=FakeArr(), media_server=server)
    item = add_item(db, sonarr_id=10)

    call_delete(db, [item.id], delete_files=False, delete_from_media_server=False)

    assert server.rescans == 0


# ---------------------------------------------------------------------------
# Dry run — what the purge would do, before it does it
# ---------------------------------------------------------------------------

def call_plan(db, item_ids, **flags):
    body = actions.DeleteRequest(item_ids=item_ids, dry_run=True, **flags)
    response = actions.bulk_delete(body, request=None, db=db)
    return json.loads(response.body)["plans"]


def test_dry_run_deletes_nothing(db, monkeypatch):
    """The whole point: a purge of dozens of items can be read before it runs."""
    arr = FakeArr()
    server = RescanningServer()
    wire(monkeypatch, sonarr=arr, media_server=server)
    item = add_item(db, sonarr_id=10)

    call_plan(db, [item.id], delete_files=True, delete_from_media_server=True)

    assert arr.deleted == []
    assert server.deleted == []
    assert server.rescans == 0
    assert db.query(MediaItem).count() == 1          # still in the library
    assert db.query(ActionLog).count() == 0          # and nothing was logged as done


def test_dry_run_names_the_arr_target(db, monkeypatch):
    wire(monkeypatch, sonarr=FakeArr())
    item = add_item(db, sonarr_id=42)

    plan = call_plan(db, [item.id], delete_files=True)[0]

    assert any("Sonarr: delete series 42" in a for a in plan["actions"])
    assert "and its files" in plan["actions"][0]


def test_dry_run_surfaces_the_same_skip_reason_as_the_real_run(db, monkeypatch):
    """An unmatched item is the failure this app exists to make visible."""
    wire(monkeypatch, sonarr=FakeArr())
    item = add_item(db, sonarr_id=None)

    plan = call_plan(db, [item.id])

    assert "not matched to any Sonarr series" in plan[0]["warnings"][0]
    assert plan[0]["actions"] == []


def test_dry_run_reports_the_space_at_stake(db, monkeypatch):
    wire(monkeypatch, sonarr=FakeArr())
    item = add_item(db, sonarr_id=10, file_size_bytes=5_000_000_000)

    assert call_plan(db, [item.id], delete_files=True)[0]["would_free_bytes"] == 5_000_000_000
    assert call_plan(db, [item.id], delete_files=False)[0]["would_free_bytes"] == 0


def test_dry_run_flags_a_missing_item_without_failing(db, monkeypatch):
    wire(monkeypatch, sonarr=FakeArr())

    plan = call_plan(db, [999])[0]

    assert plan["warnings"] == ["Not found"]


# ---------------------------------------------------------------------------
# Partial outcomes — the media is gone even when a side task failed
# ---------------------------------------------------------------------------

def test_a_refusing_media_server_no_longer_keeps_a_deleted_item(db, monkeypatch):
    """The 28/10 case: Radarr took the movie and its files, Plex refused to drop
    its own entry, and the item stayed in the library for media that was gone."""
    arr = FakeArr()
    server = FakeMediaServer(result=False)
    wire(monkeypatch, sonarr=arr, media_server=server)
    item = add_item(db, sonarr_id=10)

    results = call_delete(db, [item.id], delete_files=True,
                          delete_from_media_server=True)

    assert results[0]["status"] == "partial"
    assert results[0]["media_removed"] is True
    assert db.query(MediaItem).count() == 0      # the row goes with the media
    assert "HTTP 400" in results[0]["errors"][0]


def test_a_partial_outcome_names_what_worked_and_what_did_not(db, monkeypatch):
    wire(monkeypatch, sonarr=FakeArr(), media_server=FakeMediaServer(result=False))
    item = add_item(db, sonarr_id=10)

    outcomes = call_delete(db, [item.id], delete_files=True,
                           delete_from_media_server=True)[0]["outcomes"]

    par_cible = {o["target"]: o["status"] for o in outcomes}
    assert par_cible == {"sonarr": "done", "media_server": "failed"}


def test_nothing_removed_means_the_item_stays(db, monkeypatch):
    """The regression guard: no primary target succeeded, so the arr tool still
    holds it and dropping the row would hide that."""
    wire(monkeypatch, sonarr=FakeArr(result=False))
    item = add_item(db, sonarr_id=10)

    results = call_delete(db, [item.id], delete_files=True)

    assert results[0]["status"] == "failed"
    assert results[0]["media_removed"] is False
    assert db.query(MediaItem).count() == 1


def test_a_failed_side_task_does_not_hold_the_item(db, monkeypatch):
    """A torrent that cannot be found is worth reporting, not worth keeping a row."""
    from app.services import deletion

    class NoTorrents:
        def find_torrents_by_hashes(self, hashes):
            return []

        def find_torrents_for_title(self, title):
            return []

    item = add_item(db, sonarr_id=10)
    outcome = deletion.delete_item(
        item,
        deletion.Options(from_arr=True, files=True, torrent=True),
        deletion.Connectors(sonarr=FakeArr(), qbit=NoTorrents()),
    )

    assert outcome.status == "partial"
    assert outcome.media_removed is True          # the row would go with the media
    assert "no download id" in outcome.errors[0]


def test_the_action_log_keeps_the_per_target_outcome(db, monkeypatch):
    """Which target failed has to survive the run, or there is nothing to resume."""
    wire(monkeypatch, sonarr=FakeArr(), media_server=FakeMediaServer(result=False))
    item = add_item(db, sonarr_id=10)

    call_delete(db, [item.id], delete_files=True, delete_from_media_server=True)

    details = json.loads(db.query(ActionLog).one().details)
    assert details["status"] == "partial"
    assert details["media_removed"] is True
    assert {o["target"] for o in details["outcomes"]} == {"sonarr", "media_server"}


# --- what holds for the whole batch, said once -------------------------------

def _journal(db, cible, statut, jours=1):
    from datetime import datetime, timedelta
    db.add(ActionLog(
        timestamp=datetime.utcnow() - timedelta(days=jours),
        media_title="Peu importe", media_type="movie", action="delete",
        triggered_by="manual", success=False,
        details=json.dumps({"outcomes": [{"target": cible, "status": statut}]}),
    ))
    db.commit()


def test_a_media_server_that_keeps_refusing_is_reported_once(db):
    """Eight identical refusals on eight rows is what buried the useful part."""
    from app.services import deletion

    for _ in range(8):
        _journal(db, "media_server", "failed")
    _journal(db, "qbittorrent", "failed")          # une autre cible, hors du compte

    assert actions._media_server_refusals(db) == 8

    notes = actions._batch_notes(
        db,
        deletion.Options(from_media_server=True),
        deletion.Connectors(media_server=object(), media_server_name="Plex"),
    )
    assert len(notes) == 1
    assert "Plex refused 8 deletion(s)" in notes[0]
    assert "write access" in notes[0]


def test_nothing_is_said_when_the_option_is_off_or_nothing_failed(db):
    from app.services import deletion

    _journal(db, "media_server", "failed")
    # Option decochee : la note n'a pas lieu d'etre, meme avec un historique.
    assert actions._batch_notes(
        db, deletion.Options(from_media_server=False),
        deletion.Connectors(media_server=object(), media_server_name="Plex")) == []
    # Option cochee mais aucun serveur configure : rien a dire non plus.
    assert actions._batch_notes(
        db, deletion.Options(from_media_server=True),
        deletion.Connectors(media_server=None, media_server_name="Plex")) == []


def test_old_refusals_fall_out_of_the_window(db):
    _journal(db, "media_server", "failed", jours=45)
    assert actions._media_server_refusals(db) == 0
