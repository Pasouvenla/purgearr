"""
Unit tests for rules_engine.evaluate_rule()

These tests cover the filtering logic without touching the database or any
external service. Each test builds minimal mock Rule and MediaItem objects
using SimpleNamespace so there are no SQLAlchemy dependencies.
"""
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services.rules_engine import evaluate_rule


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_rule(**kwargs) -> SimpleNamespace:
    """Return a mock Rule with sensible defaults — override with kwargs."""
    defaults = dict(
        media_type="both",
        condition_days_unwatched=30,
        condition_watch_status=None,
        condition_min_size_gb=None,
        condition_max_size_gb=None,
        condition_resolution=None,
        condition_genres=None,
        condition_tags=None,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def make_item(**kwargs) -> SimpleNamespace:
    """Return a mock MediaItem with sensible defaults — override with kwargs."""
    defaults = dict(
        id=1,
        title="Test Movie",
        media_type="movie",
        last_watched=None,
        watch_status="unwatched",
        file_size_bytes=10 * 1024 ** 3,  # 10 GB
        resolution="1080p",
        genres=json.dumps(["Action"]),
        tags=json.dumps([]),
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# Media type filter
# ---------------------------------------------------------------------------

def test_media_type_movie_excludes_series():
    rule = make_rule(media_type="movie")
    items = [make_item(media_type="series")]
    assert evaluate_rule(rule, items) == []


def test_media_type_series_excludes_movies():
    rule = make_rule(media_type="series")
    items = [make_item(media_type="movie")]
    assert evaluate_rule(rule, items) == []


def test_media_type_both_includes_all():
    rule = make_rule(media_type="both")
    items = [make_item(media_type="movie"), make_item(media_type="series")]
    assert len(evaluate_rule(rule, items)) == 2


# ---------------------------------------------------------------------------
# Days unwatched cutoff
# ---------------------------------------------------------------------------

def test_recently_watched_excluded():
    rule = make_rule(condition_days_unwatched=30)
    item = make_item(last_watched=datetime.utcnow() - timedelta(days=5))
    assert evaluate_rule(rule, [item]) == []


def test_old_enough_included():
    rule = make_rule(condition_days_unwatched=30)
    item = make_item(last_watched=datetime.utcnow() - timedelta(days=45))
    assert len(evaluate_rule(rule, [item])) == 1


def test_never_watched_included_by_default():
    rule = make_rule(condition_days_unwatched=30)
    item = make_item(last_watched=None, watch_status="unwatched")
    assert len(evaluate_rule(rule, [item])) == 1


# ---------------------------------------------------------------------------
# In-progress items always excluded
# ---------------------------------------------------------------------------

def test_in_progress_always_excluded():
    rule = make_rule(condition_days_unwatched=1)
    item = make_item(
        last_watched=datetime.utcnow() - timedelta(days=365),
        watch_status="in_progress",
    )
    assert evaluate_rule(rule, [item]) == []


# ---------------------------------------------------------------------------
# Watch status condition
# ---------------------------------------------------------------------------

def test_watch_status_never_watched_requires_no_last_watched():
    rule = make_rule(condition_watch_status="never_watched")
    # Has a last_watched date → should be excluded even if status is "unwatched"
    item = make_item(
        watch_status="unwatched",
        last_watched=datetime.utcnow() - timedelta(days=100),
    )
    assert evaluate_rule(rule, [item]) == []


def test_watch_status_never_watched_passes_for_truly_new():
    rule = make_rule(condition_watch_status="never_watched")
    item = make_item(watch_status="unwatched", last_watched=None)
    assert len(evaluate_rule(rule, [item])) == 1


def test_watch_status_unwatched_excludes_watched():
    rule = make_rule(condition_watch_status="unwatched")
    item = make_item(watch_status="watched", last_watched=None)
    assert evaluate_rule(rule, [item]) == []


# ---------------------------------------------------------------------------
# Size conditions
# ---------------------------------------------------------------------------

def test_min_size_excludes_small_items():
    rule = make_rule(condition_min_size_gb=20)
    item = make_item(file_size_bytes=5 * 1024 ** 3)  # 5 GB
    assert evaluate_rule(rule, [item]) == []


def test_min_size_includes_large_enough():
    rule = make_rule(condition_min_size_gb=5)
    item = make_item(file_size_bytes=10 * 1024 ** 3)  # 10 GB
    assert len(evaluate_rule(rule, [item])) == 1


def test_max_size_excludes_large_items():
    rule = make_rule(condition_max_size_gb=5)
    item = make_item(file_size_bytes=10 * 1024 ** 3)  # 10 GB
    assert evaluate_rule(rule, [item]) == []


def test_max_size_includes_small_enough():
    rule = make_rule(condition_max_size_gb=20)
    item = make_item(file_size_bytes=10 * 1024 ** 3)  # 10 GB
    assert len(evaluate_rule(rule, [item])) == 1


def test_size_none_treated_as_zero():
    rule = make_rule(condition_min_size_gb=1)
    item = make_item(file_size_bytes=None)
    assert evaluate_rule(rule, [item]) == []


# ---------------------------------------------------------------------------
# Resolution filter
# ---------------------------------------------------------------------------

def test_resolution_match():
    rule = make_rule(condition_resolution=json.dumps(["1080p", "4k"]))
    item = make_item(resolution="4k")
    assert len(evaluate_rule(rule, [item])) == 1


def test_resolution_no_match():
    rule = make_rule(condition_resolution=json.dumps(["4k"]))
    item = make_item(resolution="1080p")
    assert evaluate_rule(rule, [item]) == []


def test_resolution_case_insensitive():
    rule = make_rule(condition_resolution=json.dumps(["4K"]))
    item = make_item(resolution="4k")
    assert len(evaluate_rule(rule, [item])) == 1


# ---------------------------------------------------------------------------
# Genre filter
# ---------------------------------------------------------------------------

def test_genre_match():
    rule = make_rule(condition_genres=json.dumps(["action"]))
    item = make_item(genres=json.dumps(["Action", "Thriller"]))
    assert len(evaluate_rule(rule, [item])) == 1


def test_genre_no_match():
    rule = make_rule(condition_genres=json.dumps(["comedy"]))
    item = make_item(genres=json.dumps(["Action", "Thriller"]))
    assert evaluate_rule(rule, [item]) == []


def test_genre_case_insensitive():
    rule = make_rule(condition_genres=json.dumps(["ACTION"]))
    item = make_item(genres=json.dumps(["action"]))
    assert len(evaluate_rule(rule, [item])) == 1


def test_genre_empty_item_excluded():
    rule = make_rule(condition_genres=json.dumps(["action"]))
    item = make_item(genres=json.dumps([]))
    assert evaluate_rule(rule, [item]) == []


# ---------------------------------------------------------------------------
# Tag filter
# ---------------------------------------------------------------------------

def test_tag_match():
    rule = make_rule(condition_tags=json.dumps(["4k-only"]))
    item = make_item(tags=json.dumps(["4k-only", "french"]))
    assert len(evaluate_rule(rule, [item])) == 1


def test_tag_no_match():
    rule = make_rule(condition_tags=json.dumps(["4k-only"]))
    item = make_item(tags=json.dumps(["french"]))
    assert evaluate_rule(rule, [item]) == []


def test_tag_empty_item_excluded():
    rule = make_rule(condition_tags=json.dumps(["4k-only"]))
    item = make_item(tags=json.dumps([]))
    assert evaluate_rule(rule, [item]) == []


# ---------------------------------------------------------------------------
# Combined conditions (AND logic)
# ---------------------------------------------------------------------------

def test_all_conditions_must_pass():
    rule = make_rule(
        media_type="movie",
        condition_days_unwatched=30,
        condition_min_size_gb=5,
        condition_resolution=json.dumps(["4k"]),
        condition_genres=json.dumps(["action"]),
    )
    # Matches everything
    good = make_item(
        media_type="movie",
        last_watched=None,
        file_size_bytes=20 * 1024 ** 3,
        resolution="4k",
        genres=json.dumps(["Action"]),
    )
    # Fails only on resolution
    bad = make_item(
        media_type="movie",
        last_watched=None,
        file_size_bytes=20 * 1024 ** 3,
        resolution="1080p",
        genres=json.dumps(["Action"]),
    )
    result = evaluate_rule(rule, [good, bad])
    assert len(result) == 1
    assert result[0].resolution == "4k"
