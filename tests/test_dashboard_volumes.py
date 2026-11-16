"""
Tests for what the dashboard measures.

The page exists to answer one question: where does the space go, and what
can be removed. It used to answer a different one, because every share was
computed on the number of items. Counting puts a handful of 4K films level
with dozens of SD ones, and it dropped the "unknown" bucket entirely, which
on a real library was two thirds of the volume. These tests pin the move to
bytes, and pin the fact that what cannot be classified is stated instead of
hidden.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

import app.main as main
import app.routes.dashboard as tableau_de_bord
from app.database import Base
from app.models.media import MediaItem

GO = 1024 ** 3


def requete():
    """A bare GET, enough for the route and for url_for in the template."""
    return Request({
        "type": "http", "method": "GET", "path": "/dashboard",
        "headers": [], "query_string": b"",
        "app": main.app, "router": main.app.router,
    })


@pytest.fixture
def db():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    session = sessionmaker(bind=eng)()
    yield session
    session.close()


def media(db, titre, taille_go, resolution=None, codec=None, media_type="movie",
          last_watched=None, watch_status="unwatched"):
    item = MediaItem(
        source="plex", source_id=titre.lower().replace(" ", "-"),
        media_type=media_type, title=titre,
        file_size_bytes=int(taille_go * GO), resolution=resolution,
        video_codec=codec, last_watched=last_watched, watch_status=watch_status,
        date_added=datetime.utcnow(),
    )
    db.add(item)
    db.commit()
    return item


@pytest.fixture
def contexte(db, monkeypatch):
    """Call the route and hand back the context it would have rendered."""
    boite = {}

    def faux_rendu(request, nom, ctx, *a, **kw):
        boite.update(ctx)
        from starlette.responses import HTMLResponse
        return HTMLResponse("")

    monkeypatch.setattr(tableau_de_bord.templates, "TemplateResponse", faux_rendu)
    monkeypatch.setattr(tableau_de_bord, "require_auth",
                        lambda request, db: SimpleNamespace(username="essai"))

    def lire():
        tableau_de_bord.dashboard_page(requete(), db)
        return boite

    return lire


def test_a_share_is_a_share_of_the_bytes_not_of_the_item_count(db, contexte):
    # Four small 4K films against one heavy series: counting says 4K wins,
    # weighing says the opposite. The page must say the opposite.
    for n in range(4):
        media(db, f"Petit {n}", 5, resolution="4k")
    media(db, "Grosse serie", 180, media_type="series")

    barres = {b["label"]: b for b in contexte()["resolution_bars"]}
    assert barres["4K"]["count"] == 4
    assert barres["Not reported"]["count"] == 1
    # 20 Go contre 180 Go : 10 % contre 90 %.
    assert barres["4K"]["pct"] == 10
    assert barres["Not reported"]["pct"] == 90


def test_the_heaviest_bucket_comes_first(db, contexte):
    media(db, "Un film 4K", 10, resolution="4k")
    media(db, "Trois series", 150, media_type="series")
    media(db, "Un 1080p", 40, resolution="1080p")

    ordre = [b["label"] for b in contexte()["resolution_bars"]]
    assert ordre == ["Not reported", "1080P", "4K"]


def test_what_carries_no_resolution_is_never_dropped(db, contexte):
    # The bug this replaces: the bars only ever walked the four known
    # resolutions, so the unknown bucket, the heaviest one, was invisible.
    media(db, "Un film 4K", 10, resolution="4k")
    media(db, "Serie sans qualite", 90, media_type="series")

    ctx = contexte()
    assert any(b["unknown"] for b in ctx["resolution_bars"])
    assert ctx["unclassified_count"] == 1
    assert ctx["unclassified_size_gb"] == 90.0
    assert ctx["unclassified_pct"] == 90


def test_an_empty_library_divides_by_nothing(db, contexte):
    ctx = contexte()
    assert ctx["resolution_bars"] == []
    assert ctx["unclassified_count"] == 0
    assert ctx["unclassified_pct"] == 0
    assert ctx["most_stale_max_bytes"] == 0


def test_the_codec_chart_keeps_the_unreported_bucket(db, contexte):
    for n in range(7):
        media(db, f"Film {n}", 2, codec=f"codec{n}")
    for n in range(3):
        media(db, f"Serie {n}", 50, media_type="series")

    etiquettes = [b["label"] for b in contexte()["codec_bars"]]
    # Sept codecs distincts saturent les six places, l'inconnu passe quand meme.
    assert "Not reported" in etiquettes


def test_what_sleeps_is_listed_heaviest_first(db, contexte):
    vieux = datetime.utcnow() - timedelta(days=200)
    media(db, "Leger", 5, last_watched=vieux)
    media(db, "Lourd", 120, last_watched=vieux)
    media(db, "Moyen", 40, last_watched=None)
    # Vu hier : hors du seau des 90 jours.
    media(db, "Recent", 300, last_watched=datetime.utcnow() - timedelta(days=1))

    ctx = contexte()
    assert [i.title for i in ctx["most_stale"]] == ["Lourd", "Moyen", "Leger"]
    assert ctx["most_stale_max_bytes"] == 120 * GO


def test_the_page_renders_with_real_data(db, monkeypatch):
    # Garde-fou de gabarit : une erreur Jinja ne doit pas attendre la prod.
    monkeypatch.setattr(tableau_de_bord, "require_auth",
                        lambda request, db: SimpleNamespace(username="essai"))
    media(db, "Serie sans qualite", 90, media_type="series",
          last_watched=datetime.utcnow() - timedelta(days=200))
    media(db, "Un film 4K", 10, resolution="4k", codec="hevc")

    reponse = tableau_de_bord.dashboard_page(requete(), db)
    assert reponse.status_code == 200
    page = reponse.body.decode()
    assert "Not reported" in page
    assert "Would free" in page
    assert "hatched titles carry no resolution" in page
