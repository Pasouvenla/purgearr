"""
Unit tests for the Seerr (Overseerr/Jellyseerr) connector.

Overseerr answers HTTP 400 for any unknown query parameter, so a wrong filter
name does not surface as an error — it yields an empty result set that reads
like "this media was never requested". These tests pin the parameter names
against that failure mode.
"""
from app.connectors.seerr import SeerrConnector


def make_connector(pages):
    """Return a connector whose _get replays `pages` and records its params."""
    c = SeerrConnector("http://seerr.local:5055", "key")
    c.calls = []

    def fake_get(path, params=None):
        c.calls.append((path, params or {}))
        return pages.pop(0) if pages else None

    c._get = fake_get
    return c


def page(results, total=None):
    return {"results": results, "pageInfo": {"results": total if total is not None else len(results)}}


REQUEST = {"id": 611, "type": "tv", "status": 5,
           "media": {"tmdbId": 209867, "tvdbId": 1, "title": "Frieren"}}


# ---------------------------------------------------------------------------
# Query parameters
# ---------------------------------------------------------------------------

def test_media_type_uses_the_mediaType_parameter():
    """`type` is rejected with HTTP 400 by the Overseerr API."""
    c = make_connector([page([REQUEST])])
    c.get_requests(media_type="tv")
    _, params = c.calls[0]
    assert params["mediaType"] == "tv"
    assert "type" not in params


def test_status_uses_the_filter_parameter():
    """`status` is rejected with HTTP 400 by the Overseerr API."""
    c = make_connector([page([REQUEST])])
    c.get_requests(status="pending")
    _, params = c.calls[0]
    assert params["filter"] == "pending"
    assert "status" not in params


def test_unfiltered_call_sends_no_filter_keys():
    c = make_connector([page([REQUEST])])
    c.get_requests()
    _, params = c.calls[0]
    assert set(params) == {"take", "skip", "sort"}


# ---------------------------------------------------------------------------
# Pagination and shaping
# ---------------------------------------------------------------------------

def test_results_are_flattened_to_the_expected_shape():
    c = make_connector([page([REQUEST])])
    out = c.get_requests()
    assert out == [{"id": 611, "type": "tv", "status": 5,
                    "media": {"tmdbId": 209867, "tvdbId": 1, "title": "Frieren"}}]


def test_pagination_walks_every_page():
    first = page([dict(REQUEST, id=i) for i in range(100)], total=150)
    second = page([dict(REQUEST, id=i) for i in range(100, 150)], total=150)
    c = make_connector([first, second])
    assert len(c.get_requests()) == 150
    assert c.calls[0][1]["skip"] == 0
    assert c.calls[1][1]["skip"] == 100


def test_empty_response_terminates_cleanly():
    assert make_connector([page([])]).get_requests() == []


def test_failed_request_returns_empty_list():
    assert make_connector([None]).get_requests() == []


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------

def test_find_request_matches_on_tmdb_id():
    c = make_connector([page([REQUEST])])
    assert c.find_request_for_media(209867, "series")["id"] == 611


def test_find_request_returns_none_when_absent():
    c = make_connector([page([REQUEST])])
    assert c.find_request_for_media(11111, "series") is None


def test_find_request_maps_series_to_the_tv_media_type():
    c = make_connector([page([REQUEST])])
    c.find_request_for_media(209867, "series")
    assert c.calls[0][1]["mediaType"] == "tv"


def test_find_request_maps_movie_to_the_movie_media_type():
    c = make_connector([page([])])
    c.find_request_for_media(1, "movie")
    assert c.calls[0][1]["mediaType"] == "movie"
