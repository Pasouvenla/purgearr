"""
Tests for the torrent lookup behind a deletion.

Matching torrents by title substring is wrong by construction: release names
substitute separators, so 'X-Men' never appears in 'X.Men.2000.MULTi.2160p'
and the torrent survives the purge, still holding the data. The arr tools
record the infohash of every grab, which ties an item to its torrents without
guessing — and reaches cross-seed companions, which live in their own category
and are invisible to the arr tools.

Finding nothing at all is reported rather than passed over: silence here is
what left orphaned torrents seeding after a library purge.
"""
from types import SimpleNamespace

from app.connectors.qbittorrent import QBittorrentConnector
from app.services import deletion


class FakeQbit(QBittorrentConnector):
    """Real lookup logic, canned torrent list, deletions recorded."""

    def __init__(self, torrents, delete_result=True):
        self._torrents = torrents
        self._delete_result = delete_result
        self.deleted = None

    def get_torrents(self):
        return self._torrents

    def delete_torrents(self, hashes, delete_files=False):
        self.deleted = (sorted(h.lower() for h in hashes), delete_files)
        return self._delete_result


class FakeArr:
    """Only what the deletion service calls: the history and the deletion.

    `delete_files=True` implies touching the arr tool — that is the original
    semantics — so the arr branch runs even in these torrent-focused tests.
    """

    def __init__(self, download_ids):
        self._ids = download_ids

    def get_download_ids(self, arr_id):
        return self._ids

    def delete_movie(self, arr_id, delete_files=False):
        return True

    delete_series = delete_movie


def torrent(h, name, category=""):
    return {"hash": h, "name": name, "size": 1, "state": "stalledUP",
            "save_path": "/downloads", "category": category}


FILM = SimpleNamespace(media_type="movie", radarr_id=7, sonarr_id=None, title="X-Men")


# --- the flaw the infohash lookup exists to fix -----------------------------

def test_title_lookup_misses_a_release_that_spells_the_title_differently():
    qbit = FakeQbit([torrent("AAA", "X.Men.2000.MULTi.VFF.2160p.BluRay.mkv")])
    assert qbit.find_torrents_for_title("X-Men") == []


def test_infohash_lookup_finds_it():
    qbit = FakeQbit([torrent("AAA", "X.Men.2000.MULTi.VFF.2160p.BluRay.mkv")])
    assert [t["hash"] for t in qbit.find_torrents_by_hashes(["AAA"])] == ["AAA"]


def test_infohash_comparison_ignores_case():
    """The arr tools report hashes uppercase, qBittorrent lowercase."""
    qbit = FakeQbit([torrent("d950eefd31", "whatever")])
    assert len(qbit.find_torrents_by_hashes(["D950EEFD31"])) == 1


def test_infohash_lookup_reaches_cross_seed_companions():
    """A companion sits in radarr.cross and holds the same data."""
    qbit = FakeQbit([
        torrent("AAA", "Film.2024.mkv", category="radarr"),
        torrent("BBB", "Film.2024.mkv", category="radarr.cross"),
    ])
    found = {t["hash"] for t in qbit.find_torrents_by_hashes(["AAA", "BBB"])}
    assert found == {"AAA", "BBB"}


def test_no_hashes_matches_nothing_rather_than_everything():
    qbit = FakeQbit([torrent("AAA", "Film.2024.mkv")])
    assert qbit.find_torrents_by_hashes([]) == []
    assert qbit.find_torrents_by_hashes([None, ""]) == []


# --- what the deletion service does with it --------------------------------

def purge(qbit, download_ids, files):
    """Run a torrent-only deletion and return the errors it reports."""
    return deletion.delete_item(
        FILM,
        deletion.Options(from_arr=False, torrent=True, files=files),
        deletion.Connectors(qbit=qbit, radarr=FakeArr(download_ids)),
    ).errors


def test_deletion_uses_the_infohash_the_arr_tool_recorded():
    qbit = FakeQbit([torrent("AAA", "X.Men.2000.MULTi.2160p.mkv")])
    assert purge(qbit, ["AAA"], files=True) == []
    assert qbit.deleted == (["aaa"], True)


def test_a_recorded_infohash_that_is_gone_is_an_answer_not_an_error():
    """The arr tool named the torrent and it is no longer there: nothing holds the files.

    This case used to be reported as a failure alongside the genuinely unknown
    one, which is how a reassuring result and a dangerous one came to look
    identical in the interface.
    """
    qbit = FakeQbit([torrent("ZZZ", "Something.Else.mkv")])
    outcome = deletion.delete_item(
        FILM,
        deletion.Options(from_arr=False, torrent=True, files=True),
        deletion.Connectors(qbit=qbit, radarr=FakeArr(["AAA"])),
    )
    assert outcome.errors == []
    assert qbit.deleted is None
    message = [o.message for o in outcome.outcomes if o.target == "qbittorrent"][0]
    assert "Nothing to remove" in message and "1 infohash" in message


def test_deletion_falls_back_to_the_title_when_no_infohash_is_known():
    """An item the arr tool no longer knows still gets a best effort."""
    qbit = FakeQbit([torrent("AAA", "X-Men.2000.mkv")])
    assert purge(qbit, [], files=False) == []
    assert qbit.deleted == (["aaa"], False)


def test_an_unknown_infohash_says_so_instead_of_claiming_nothing_was_found():
    """The wording that cost 11.99 GB of silent seeding on 2026-11-12.

    "No torrent found" reads as "we looked and there is none". With no download
    id recorded there was nothing to look up, and the title guess proves
    nothing, which has to be said in those words.
    """
    erreur = purge(FakeQbit([]), [], files=False)[0]
    assert "no download id" in erreur
    assert "may still be seeding" in erreur


def test_an_unknown_infohash_leaves_the_freed_figure_an_estimate():
    """The correctness half of the same defect.

    A title lookup that finds nothing established nothing, so the space cannot
    be reported as measured. It used to be, which is how a seeding torrent and
    a removed one produced the same dashboard figure.
    """
    options = deletion.Options(from_arr=True, torrent=True, files=True)
    # FILM est partage par les autres tests et n'a pas de taille : on en fait un
    # a nous plutot que de lui en ajouter une sous leurs pieds.
    film = SimpleNamespace(media_type="movie", radarr_id=7, sonarr_id=None,
                           title="X-Men", file_size_bytes=5 * 1024**3)

    class ArrMuet(FakeArr):
        def delete_movie(self, *a, **k):
            return True

    sans_id = deletion.delete_item(
        film, options, deletion.Connectors(qbit=FakeQbit([]), radarr=ArrMuet([])))
    taille, mesure = sans_id.freed(film, options)
    assert taille > 0 and mesure is False, "sans infohash, le chiffre est un majorant"

    avec_id = deletion.delete_item(
        film, options, deletion.Connectors(qbit=FakeQbit([]), radarr=ArrMuet(["AAA"])))
    _, mesure = avec_id.freed(film, options)
    assert mesure is True, "infohash connu et absent : plus rien ne tient le fichier"


def test_a_refused_deletion_is_reported():
    qbit = FakeQbit([torrent("AAA", "X.Men.2000.mkv")], delete_result=False)
    assert purge(qbit, ["AAA"], files=True) == ["qBittorrent deletion failed"]


# --- is the freed space a real figure or an upper bound? -------------------

ITEM = SimpleNamespace(media_type="movie", radarr_id=7, sonarr_id=None,
                       title="X-Men", file_size_bytes=6_000_000_000)


def freed_for(qbit, download_ids, torrent_option):
    options = deletion.Options(from_arr=True, files=True, torrent=torrent_option)
    result = deletion.delete_item(
        ITEM, options,
        deletion.Connectors(qbit=qbit, radarr=FakeArr(download_ids)),
    )
    return result.freed(ITEM, options)


def test_freed_is_certain_when_the_torrent_went_with_it():
    qbit = FakeQbit([torrent("AAA", "X.Men.2000.mkv")])
    assert freed_for(qbit, ["AAA"], torrent_option=True) == (6_000_000_000, True)


def test_freed_is_certain_when_no_torrent_held_the_data():
    """Nothing to seed it, so the library file was the only hardlink."""
    assert freed_for(FakeQbit([]), ["AAA"], torrent_option=True) == (6_000_000_000, True)


def test_freed_is_an_estimate_when_the_torrents_were_left_alone():
    """The purge that reported its space back while 260 GB stayed occupied."""
    qbit = FakeQbit([torrent("AAA", "X.Men.2000.mkv")])
    assert freed_for(qbit, ["AAA"], torrent_option=False) == (6_000_000_000, False)


def test_freed_is_an_estimate_when_the_torrent_deletion_was_refused():
    qbit = FakeQbit([torrent("AAA", "X.Men.2000.mkv")], delete_result=False)
    assert freed_for(qbit, ["AAA"], torrent_option=True) == (6_000_000_000, False)


def test_nothing_is_freed_when_nothing_was_removed():
    options = deletion.Options(from_arr=True, files=True)
    result = deletion.delete_item(
        ITEM, options,
        deletion.Connectors(radarr=None),      # unmatched: no primary succeeded
    )
    assert result.freed(ITEM, options) == (0, True)


def test_the_infohash_is_read_before_the_arr_tool_forgets_it():
    """The ordering defect that made every REAL purge fall back to titles.

    An arr tool has no history for an item it has just deleted. Reading the
    infohashes after the arr branch therefore came back empty every single
    time, so the lookup this module exists for never ran outside the dry run,
    which deletes nothing and kept reporting correctly. Torrents went on
    seeding after purges that claimed to have looked.

    The double forgets exactly the way the real one does. Without the fix it
    finds no torrent and reports "no download id"; with it, the torrent is
    matched by infohash and removed.
    """
    class ArrOublieux:
        def __init__(self):
            self.supprime = False

        def get_download_ids(self, arr_id):
            return [] if self.supprime else ["AAA"]

        def delete_movie(self, *a, **k):
            self.supprime = True
            return True

    arr = ArrOublieux()
    qbit = FakeQbit([torrent("AAA", "X.Men.2000.MULTi.2160p.mkv")])
    outcome = deletion.delete_item(
        FILM,
        deletion.Options(from_arr=True, torrent=True, files=True),
        deletion.Connectors(qbit=qbit, radarr=arr),
    )
    assert arr.supprime, "le film doit bien avoir ete supprime de l'arr"
    assert qbit.deleted == (["aaa"], True), "le torrent doit etre trouve par INFOHASH"
    assert outcome.errors == []
