"""
Unit tests for the Plex connector.

Covers the two things the arr cross-reference and the media-server deletion
depend on: parsing external ids out of Plex guids, and the delete calls. No
network access — the HTTP session is replaced with a recording stub.
"""
from types import SimpleNamespace

import pytest

from app.connectors.plex import PlexConnector, _to_int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeSession:
    """Records calls so tests can assert on the request that was made."""

    def __init__(self, response=None):
        self.headers = {}
        self.response = response or FakeResponse()
        self.deleted = []
        self.got = []

    def get(self, url, **kwargs):
        self.got.append((url, kwargs))
        return self.response

    def delete(self, url, **kwargs):
        self.deleted.append((url, kwargs))
        return self.response


def make_connector(response=None) -> PlexConnector:
    c = PlexConnector("http://plex.local:32400/", "token123")
    c.session = FakeSession(response)
    return c


# ---------------------------------------------------------------------------
# _to_int
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("157239", 157239), (42, 42), ("", None), (None, None), ("tt123", None),
])
def test_to_int_is_total(raw, expected):
    assert _to_int(raw) == expected


# ---------------------------------------------------------------------------
# External id extraction
# ---------------------------------------------------------------------------

def test_external_ids_parses_all_three_schemes():
    item = {"Guid": [
        {"id": "imdb://tt13623632"},
        {"id": "tmdb://157239"},
        {"id": "tvdb://458912"},
    ]}
    assert PlexConnector._external_ids(item) == {
        "tmdb_id": 157239, "tvdb_id": 458912, "imdb_id": "tt13623632",
    }


def test_external_ids_returns_nulls_when_guids_absent():
    """Plex omits Guid unless includeGuids=1 — must not raise."""
    assert PlexConnector._external_ids({}) == {
        "tmdb_id": None, "tvdb_id": None, "imdb_id": None,
    }


def test_external_ids_ignores_the_agent_specific_guid():
    """`plex://show/<hash>` cannot be cross-referenced with an arr tool."""
    item = {"guid": "plex://show/5fd4f9d1", "Guid": [{"id": "tvdb://393197"}]}
    ids = PlexConnector._external_ids(item)
    assert ids["tvdb_id"] == 393197
    assert ids["tmdb_id"] is None


def test_external_ids_skips_malformed_entries():
    item = {"Guid": [{"id": "garbage"}, {"id": "tmdb://"}, {"id": "tvdb://12"}]}
    ids = PlexConnector._external_ids(item)
    assert ids["tvdb_id"] == 12
    assert ids["tmdb_id"] is None


def test_external_ids_accepts_plain_string_entries():
    assert PlexConnector._external_ids({"Guid": ["tmdb://99"]})["tmdb_id"] == 99
# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------

def test_delete_media_targets_the_library_metadata_endpoint():
    c = make_connector()
    assert c.delete_media("17752") is None
    url, _ = c.session.deleted[0]
    assert url == "http://plex.local:32400/library/metadata/17752"


def test_delete_media_returns_a_reason_instead_of_raising():
    assert make_connector(FakeResponse(status=500)).delete_media("1") is not None


def test_400_is_explained_as_a_write_access_problem():
    """Plex answers a bare 400 when it cannot remove the files, and logs nothing.

    Hit for real on a read-only NFS mount: without this the user only sees
    "deletion failed" and has no way to guess the cause.
    """
    reason = make_connector(FakeResponse(status=400)).delete_media("1")
    assert "write access" in reason
    assert "read-only" in reason


def test_401_points_at_the_token_and_the_server_setting():
    reason = make_connector(FakeResponse(status=401)).delete_media("1")
    assert "Allow media deletion" in reason


def test_404_says_the_item_is_already_gone():
    assert "no longer knows" in make_connector(FakeResponse(status=404)).delete_media("1")


def test_unreachable_server_is_reported_as_such():
    c = make_connector()

    def boom(url, **kw):
        raise OSError("connection refused")

    c.session.delete = boom
    assert "unreachable" in c.delete_media("1")


def test_allows_media_deletion_reads_the_server_preference():
    c = make_connector(FakeResponse(payload={
        "MediaContainer": {"Setting": [
            {"id": "friendlyName", "value": "PasouPlexou"},
            {"id": "allowMediaDeletion", "value": True},
        ]}
    }))
    assert c.allows_media_deletion() is True


def test_allows_media_deletion_detects_the_disabled_case():
    c = make_connector(FakeResponse(payload={
        "MediaContainer": {"Setting": [{"id": "allowMediaDeletion", "value": False}]}
    }))
    assert c.allows_media_deletion() is False


def test_allows_media_deletion_accepts_string_booleans():
    """Plex serialises bool prefs as "1"/"0" depending on the response format."""
    c = make_connector(FakeResponse(payload={
        "MediaContainer": {"Setting": [{"id": "allowMediaDeletion", "value": "1"}]}
    }))
    assert c.allows_media_deletion() is True


def test_allows_media_deletion_is_unknown_when_pref_is_absent():
    c = make_connector(FakeResponse(payload={"MediaContainer": {"Setting": []}}))
    assert c.allows_media_deletion() is None


# ---------------------------------------------------------------------------
# forget_missing_files — the way out of the ghost entries a purge leaves
# ---------------------------------------------------------------------------

class SectionsSession(FakeSession):
    """Answers the section listing, records everything else, can refuse."""

    def __init__(self, sections, refuse_paths=()):
        super().__init__()
        self._sections = sections
        self._refuse = set(refuse_paths)

    def get(self, url, **kwargs):
        self.got.append((url, kwargs))
        if url.endswith("/library/sections"):
            return FakeResponse(payload={"MediaContainer": {"Directory": self._sections}})
        if any(url.endswith(p) for p in self._refuse):
            return FakeResponse(status=400)
        return FakeResponse()


SECTIONS = [
    {"key": "1", "title": "Films", "type": "movie"},
    {"key": "2", "title": "Series", "type": "show"},
]


def test_forget_missing_files_refreshes_and_empties_every_section():
    plex = PlexConnector("http://plex:32400", "tok")
    plex.session = SectionsSession(SECTIONS)
    assert plex.forget_missing_files() == []
    appeles = [u for u, _ in plex.session.got if "sections/" in u]
    assert appeles == [
        "http://plex:32400/library/sections/1/refresh",
        "http://plex:32400/library/sections/1/emptyTrash",
        "http://plex:32400/library/sections/2/refresh",
        "http://plex:32400/library/sections/2/emptyTrash",
    ]


def test_forget_missing_files_needs_no_delete_call():
    """This is the point: Plex refuses DELETE without write access to the media."""
    plex = PlexConnector("http://plex:32400", "tok")
    plex.session = SectionsSession(SECTIONS)
    plex.forget_missing_files()
    assert plex.session.deleted == []


def test_forget_missing_files_names_the_section_it_could_not_clean():
    plex = PlexConnector("http://plex:32400", "tok")
    plex.session = SectionsSession(SECTIONS, refuse_paths=("/sections/2/emptyTrash",))
    errors = plex.forget_missing_files()
    assert len(errors) == 1
    assert "Series" in errors[0] and "empty trash" in errors[0]


def test_forget_missing_files_survives_an_empty_library():
    plex = PlexConnector("http://plex:32400", "tok")
    plex.session = SectionsSession([])
    assert plex.forget_missing_files() == []
