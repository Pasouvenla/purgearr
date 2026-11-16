"""
Unit tests for the Sonarr/Radarr cross-reference in services.sync.

An item that fails to match an arr entry keeps `sonarr_id`/`radarr_id` NULL,
which makes it impossible to delete from the arr tool later. Matching used to
rely on the title alone, so any naming difference between the media server and
the arr tool broke it silently. These tests pin the external-id cascade that
replaced it.
"""
from app.services.sync import _index_arr_items, _match_arr_item, _normalize_title


# ---------------------------------------------------------------------------
# Title normalization
# ---------------------------------------------------------------------------

def test_normalize_strips_case_and_padding():
    assert _normalize_title("  The Expanse  ") == "the expanse"


def test_normalize_ignores_punctuation_differences():
    """The real-world mismatch: Plex vs Sonarr naming of the same show."""
    plex = _normalize_title("Fate/stay night [Unlimited Blade Works]")
    sonarr = _normalize_title("Fate/Stay Night: Unlimited Blade Works")
    assert plex == sonarr


def test_normalize_keeps_bracketed_subtitles():
    """Bracketed text often carries the franchise subtitle — dropping it would
    collapse distinct entries onto the same key."""
    assert _normalize_title("Scrubs [US]") == "scrubs us"
    assert _normalize_title("Alien (1979)") == "alien 1979"


def test_normalize_collapses_whitespace():
    assert _normalize_title("Mr.  Robot") == "mr robot"


def test_normalize_handles_empty_title():
    assert _normalize_title("") == ""


# ---------------------------------------------------------------------------
# Index construction
# ---------------------------------------------------------------------------

def test_index_registers_every_external_id():
    idx = _index_arr_items([
        {"id": 7, "title": "Alien: Earth", "tvdb_id": 458912,
         "tmdb_id": 157239, "imdb_id": "tt13623632"},
    ])
    assert idx["tvdb"][458912]["id"] == 7
    assert idx["tmdb"][157239]["id"] == 7
    assert idx["imdb"]["tt13623632"]["id"] == 7
    assert idx["title"]["alien earth"]["id"] == 7


def test_index_skips_missing_ids_without_failing():
    idx = _index_arr_items([{"id": 1, "title": "Lucky", "tvdb_id": None, "tmdb_id": 0}])
    assert idx["tvdb"] == {}
    assert idx["tmdb"] == {}
    assert idx["title"]["lucky"]["id"] == 1


def test_index_keeps_first_entry_on_duplicate_titles():
    """Two shows normalizing to the same key must not clobber each other."""
    idx = _index_arr_items([
        {"id": 1, "title": "The Office"},
        {"id": 2, "title": "The Office!"},
    ])
    assert idx["title"]["the office"]["id"] == 1


# ---------------------------------------------------------------------------
# Match cascade
# ---------------------------------------------------------------------------

SONARR = _index_arr_items([
    {"id": 42, "title": "Nagi-Asu: A Lull in the Sea",
     "tvdb_id": 272073, "tmdb_id": 61408, "imdb_id": "tt2531336"},
    {"id": 43, "title": "Frieren", "tvdb_id": 424536},
])


def test_match_prefers_tvdb_id_over_title():
    """The regression case: titles differ, tvdb id agrees."""
    arr, how = _match_arr_item(SONARR, {"tvdb_id": 272073}, "A Lull in the Sea")
    assert arr["id"] == 42
    assert how == "tvdb"


def test_match_falls_back_to_tmdb_id():
    arr, how = _match_arr_item(SONARR, {"tmdb_id": 61408}, "Totally Different Name")
    assert arr["id"] == 42
    assert how == "tmdb"


def test_match_falls_back_to_title_when_no_ids():
    arr, how = _match_arr_item(SONARR, {}, "frieren")
    assert arr["id"] == 43
    assert how == "title"


def test_match_reports_no_match_rather_than_guessing():
    arr, how = _match_arr_item(SONARR, {"tvdb_id": 999999}, "Unknown Show")
    assert arr is None
    assert how is None


def test_match_accepts_string_ids_from_the_media_server():
    """Plex guids are parsed from text, so ids may arrive as strings."""
    arr, how = _match_arr_item(SONARR, {"tvdb_id": "272073"}, "whatever")
    assert arr["id"] == 42
    assert how == "tvdb"


def test_match_survives_malformed_ids():
    arr, how = _match_arr_item(SONARR, {"tvdb_id": "not-a-number"}, "frieren")
    assert arr["id"] == 43
    assert how == "title"


def test_match_against_empty_index_is_safe():
    arr, how = _match_arr_item(_index_arr_items([]), {"tvdb_id": 1}, "Anything")
    assert arr is None
    assert how is None
