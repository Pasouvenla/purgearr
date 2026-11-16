"""
Tests for acting on one season without touching the others.

Sonarr ignores seasonNumber on /episodefile: asking for one season's files
returns every file of the series. Measured against a live instance on a three
season show, both requests answered with the same 59 records. The connector
used to hand that whole list to the bulk delete, so the per season button
erased the series. These tests pin the filter that had to move client side,
and the scoping of the torrent lookup that goes with it.
"""
from app.connectors.sonarr import SonarrConnector


class FauxSonarr(SonarrConnector):
    """A connector whose HTTP layer is replaced by canned answers."""

    def __init__(self, fichiers=None, historique=None, get_renvoie_none=False):
        self.url = "http://sonarr.invalid"
        self.fichiers = fichiers if fichiers is not None else []
        self.historique = historique if historique is not None else []
        self.get_renvoie_none = get_renvoie_none
        self.supprimes = None
        self.params_vus = {}

    def _get(self, path, params=None):
        self.params_vus[path] = params or {}
        if self.get_renvoie_none:
            return None
        if path == "/episodefile":
            return self.fichiers
        if path == "/history/series":
            return self.historique
        return None

    def _delete_json(self, path, json=None):
        self.supprimes = (json or {}).get("episodeFileIds")
        return True


def fichier(ident, saison):
    return {"id": ident, "seasonNumber": saison, "seriesId": 12}


def evenement(infohash, saison):
    return {"downloadId": infohash, "episode": {"seasonNumber": saison}}


# --- Suppression des fichiers d'une saison ---------------------------------

def test_only_the_files_of_that_season_are_deleted():
    arr = FauxSonarr(fichiers=[fichier(1, 1), fichier(2, 1), fichier(3, 2), fichier(4, 3)])

    assert arr.delete_season_files(12, 1) is True
    assert arr.supprimes == [1, 2]


def test_the_other_seasons_survive_a_deletion():
    # Le defaut reproduit : sans filtre cote client, les quatre partaient.
    arr = FauxSonarr(fichiers=[fichier(1, 1), fichier(2, 2), fichier(3, 3), fichier(4, 3)])

    arr.delete_season_files(12, 3)
    assert arr.supprimes == [3, 4]


def test_the_season_filter_is_not_sent_to_sonarr():
    # L'envoyer entretiendrait l'illusion qu'il sert a quelque chose.
    arr = FauxSonarr(fichiers=[fichier(1, 1)])

    arr.delete_season_files(12, 1)
    assert "seasonNumber" not in arr.params_vus["/episodefile"]


def test_a_season_with_no_file_deletes_nothing_and_says_so():
    arr = FauxSonarr(fichiers=[fichier(1, 1)])

    assert arr.delete_season_files(12, 4) is True
    assert arr.supprimes is None


def test_an_unreachable_sonarr_is_a_failure_not_an_empty_library():
    # Ancien comportement : une requete en echec repondait True, donc
    # l'interface annoncait une suppression qui n'avait pas eu lieu.
    arr = FauxSonarr(get_renvoie_none=True)

    assert arr.delete_season_files(12, 1) is False
    assert arr.supprimes is None


# --- Recherche des torrents d'une saison -----------------------------------

def test_the_torrent_lookup_narrows_to_the_season():
    arr = FauxSonarr(historique=[evenement("aaa", 1), evenement("bbb", 2), evenement("ccc", 1)])

    assert arr.get_download_ids(12, season_number=1) == ["aaa", "ccc"]


def test_an_event_that_cannot_prove_its_season_is_left_out():
    # Un torrent qu'on ne sait pas situer est un torrent qu'on ne supprime pas.
    arr = FauxSonarr(historique=[evenement("aaa", 1), {"downloadId": "sans-episode"}])

    assert arr.get_download_ids(12, season_number=1) == ["aaa"]


def test_without_a_season_the_whole_series_is_looked_up():
    arr = FauxSonarr(historique=[evenement("aaa", 1), {"downloadId": "sans-episode"}])

    assert arr.get_download_ids(12) == ["aaa", "sans-episode"]
    assert "seasonNumber" not in arr.params_vus["/history/series"]


def test_the_same_torrent_is_only_listed_once():
    arr = FauxSonarr(historique=[evenement("aaa", 1), evenement("aaa", 1)])

    assert arr.get_download_ids(12, season_number=1) == ["aaa"]
