"""The one place a deletion is carried out.

Two routes used to do this: the manual bulk delete and the rule engine. Each
carried its own copy of the connector setup, the per-target branches and the
bookkeeping, so every fix had to be written twice and the two drifted.

It also replaces the all-or-nothing verdict. A deletion touches up to five
systems and they fail independently: the arr tool can remove a movie and its
files while the media server refuses to drop its own entry. Reporting that as
a plain failure, and keeping the item in the library because of it, left rows
for media that no longer existed. Each target now reports its own outcome, and
the item leaves the library when the media is actually gone — not when every
side task also happened to succeed.
"""
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

DONE = "done"
FAILED = "failed"
SKIPPED = "skipped"

# Targets that make the media itself disappear. The others (torrent, Seerr
# request) are clean-up around it: their failure is worth reporting but must
# not keep an item in the library for media that is already gone.
PRIMARY = ("sonarr", "radarr", "media_server")


@dataclass
class Outcome:
    target: str
    status: str
    message: Optional[str] = None


@dataclass
class Options:
    """What the caller asked for, in one vocabulary for both routes."""
    from_arr: bool = True
    files: bool = False
    torrent: bool = False
    seerr_request: bool = False
    from_media_server: bool = False

    @property
    def touches_arr(self) -> bool:
        return self.from_arr or self.files

    @property
    def removes_files(self) -> bool:
        return self.files or self.from_media_server


@dataclass
class Connectors:
    sonarr: object = None
    radarr: object = None
    qbit: object = None
    seerr: object = None
    media_server: object = None
    media_server_name: str = "Plex"


@dataclass
class Result:
    outcomes: list = field(default_factory=list)
    seerr_deleted: bool = False
    # True when the torrent lookup ran and came back empty: nothing holds a
    # second hardlink, so the space really is freed.
    no_torrent_held_it: bool = False

    def add(self, target: str, status: str, message: Optional[str] = None):
        self.outcomes.append(Outcome(target, status, message))

    @property
    def failures(self) -> list:
        return [o for o in self.outcomes if o.status == FAILED]

    @property
    def errors(self) -> list:
        return [o.message for o in self.failures if o.message]

    @property
    def media_removed(self) -> bool:
        """Whether the media itself is gone, whatever else went wrong."""
        return any(o.target in PRIMARY and o.status == DONE for o in self.outcomes)

    @property
    def status(self) -> str:
        """done when nothing failed, partial when something still worked."""
        if not self.failures:
            return DONE
        return "partial" if any(o.status == DONE for o in self.outcomes) else FAILED

    @property
    def success(self) -> bool:
        """Kept for the action log, which stores a boolean."""
        return self.status == DONE

    def freed(self, item, options: "Options") -> tuple:
        """Bytes freed, and whether that figure can be trusted.

        The library file and the torrent are usually the same inode: the arr
        tools import by hardlink. Removing the library side alone frees
        nothing while a torrent keeps seeding it — which is how a purge of
        36 films once reported its space back while 260 GB stayed occupied.

        The figure is only certain when the torrents were removed in the same
        breath. Otherwise it is an upper bound, and says so rather than
        inflating a dashboard.
        """
        if not (self.media_removed and options.removes_files):
            return 0, True
        taille = item.file_size_bytes or 0
        if not options.torrent:
            # Nobody looked, so nobody knows whether a torrent keeps seeding it.
            return taille, False
        torrent_parti = any(
            o.target == "qbittorrent" and o.status == DONE for o in self.outcomes
        )
        return taille, torrent_parti or self.no_torrent_held_it

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "media_removed": self.media_removed,
            "outcomes": [
                {"target": o.target, "status": o.status, "message": o.message}
                for o in self.outcomes
            ],
        }


def arr_skip_reason(item, connectors: Connectors) -> str:
    """Why an arr deletion could not even be attempted.

    User-facing: the item stays in the arr tool until this is resolved, so the
    message names the cause rather than failing blankly.
    """
    if item.media_type == "series":
        if not connectors.sonarr:
            return "Sonarr is not configured - nothing was deleted from Sonarr"
        if not item.sonarr_id:
            return (
                f"'{item.title}' is not matched to any Sonarr series "
                "(no tvdb/tmdb/imdb id and no title match) - nothing was deleted from Sonarr"
            )
    else:
        if not connectors.radarr:
            return "Radarr is not configured - nothing was deleted from Radarr"
        if not item.radarr_id:
            return (
                f"'{item.title}' is not matched to any Radarr movie "
                "(no tmdb/imdb id and no title match) - nothing was deleted from Radarr"
            )
    return "No arr target for this item - nothing was deleted"


def download_ids_for(item, connectors: Connectors) -> list:
    if item.media_type == "series" and item.sonarr_id and connectors.sonarr:
        return connectors.sonarr.get_download_ids(item.sonarr_id)
    if item.media_type == "movie" and item.radarr_id and connectors.radarr:
        return connectors.radarr.get_download_ids(item.radarr_id)
    return []


def nothing_left_message(item, ids) -> str:
    combien = f"{len(ids)} infohash" + ("es" if len(ids) > 1 else "")
    return (
        f"Nothing to remove: the arr tool recorded {combien} for '{item.title}' "
        "and none of them is in qBittorrent any more, so no torrent was holding its files"
    )


def no_infohash_message(item) -> str:
    return (
        f"The arr history holds no download id for '{item.title}', so its torrent could "
        "not be looked up by infohash, and a guess by title found nothing. A torrent may "
        "still be seeding these files: this does NOT mean there is none"
    )


def find_torrents(item, connectors: Connectors):
    """The torrents behind an item, how they were looked up, and how many ids were known.

    By infohash, which the arr tools record for every grab. The title lookup
    kept as a fallback is unreliable by construction: 'X-Men' does not appear
    in 'X.Men.2000.MULTi.2160p'.

    The count of known ids is returned because the two empty results mean
    opposite things. An infohash lookup that finds nothing is an ANSWER: the arr
    tool knew which torrents to look for and none of them is there any more, so
    nothing holds the files. A title lookup that finds nothing is an ABSENCE OF
    ANSWER: there was no id to look up, and the guess proves nothing. Reporting
    both as "no torrent found" is what let a seeding torrent pass for a removed
    one on 2026-11-12.
    """
    ids = download_ids_for(item, connectors)
    if ids:
        return connectors.qbit.find_torrents_by_hashes(ids), "infohash", ids
    return connectors.qbit.find_torrents_for_title(item.title), "title", ids


def delete_from_media_server(item, connectors: Connectors) -> Optional[str]:
    """Delete an item's files through the media server.

    Returns None on success, or a user-facing message. Both servers refuse
    deletion with an auth-looking error when the feature is disabled, so the
    permission is read first to avoid a misleading message.
    """
    connector = connectors.media_server
    name = connectors.media_server_name
    if connector is None:
        return f"{name} is not configured - its files were not deleted"
    if connector.allows_media_deletion() is False:
        return (
            f"{name} refuses deletions - enable media deletion in its settings "
            "before retrying"
        )
    reason = (
        connector.delete_item(item.source_id)
        if name == "Jellyfin"
        else connector.delete_media(item.source_id)
    )
    return f"'{item.title}': {reason}" if reason else None


def delete_item(item, options: Options, connectors: Connectors) -> Result:
    """Carry out a deletion, reporting each target separately."""
    result = Result()

    # ⚠️⚠️ RESOLVED FIRST, BEFORE ANYTHING IS DELETED, and the order is the whole
    # point. The infohashes come from the arr tool's own history, and an arr tool
    # no longer has the history of an item it has just deleted. Looking them up
    # after the arr branch returned nothing EVERY time, so every real deletion
    # fell back to the title guess that the infohash lookup exists to replace,
    # while the dry run — which deletes nothing — kept reporting correctly. That
    # is why torrents went on seeding after a purge that said it had looked.
    # Measured on the live instance on 2026-11-12: an existing movie yields its
    # infohashes (1 to 38 of them), an id that is gone yields none.
    torrents, lookup, ids = [], "infohash", []
    cherche_torrents = options.torrent and connectors.qbit is not None
    if cherche_torrents:
        torrents, lookup, ids = find_torrents(item, connectors)

    if options.touches_arr:
        if item.media_type == "series" and item.sonarr_id and connectors.sonarr:
            ok = connectors.sonarr.delete_series(item.sonarr_id, delete_files=options.files)
            result.add("sonarr", DONE if ok else FAILED,
                       None if ok else "Sonarr deletion failed")
        elif item.media_type == "movie" and item.radarr_id and connectors.radarr:
            ok = connectors.radarr.delete_movie(item.radarr_id, delete_files=options.files)
            result.add("radarr", DONE if ok else FAILED,
                       None if ok else "Radarr deletion failed")
        else:
            # Never silent: an unmatched item left unreported used to be logged
            # as deleted and dropped from the library while the arr tool kept
            # it, and kept re-downloading it.
            target = "sonarr" if item.media_type == "series" else "radarr"
            result.add(target, FAILED, arr_skip_reason(item, connectors))

    if options.from_media_server:
        error = delete_from_media_server(item, connectors)
        result.add("media_server", FAILED if error else DONE, error)

    if options.torrent:
        if connectors.qbit is None:
            result.add("qbittorrent", SKIPPED, None)
        else:
            if not torrents and lookup == "infohash":
                # An answer, not a failure to look: the arr tool named the
                # torrents and none is left, so nothing holds a second hardlink
                # and the space really is freed.
                result.no_torrent_held_it = True
                result.add("qbittorrent", SKIPPED, nothing_left_message(item, ids))
            elif not torrents:
                # No id to look up, so the title guess proves nothing. Do NOT
                # set no_torrent_held_it here: claiming the space was measured
                # is how 11.99 GB kept seeding while being reported as freed.
                result.add("qbittorrent", FAILED, no_infohash_message(item))
            else:
                ok = connectors.qbit.delete_torrents(
                    [t["hash"] for t in torrents], delete_files=options.files
                )
                result.add("qbittorrent", DONE if ok else FAILED,
                           None if ok else "qBittorrent deletion failed")

    if options.seerr_request:
        if connectors.seerr is None:
            result.add("seerr", SKIPPED, None)
        elif not item.tmdb_id:
            # Staying silent here used to report a Seerr clean-up that never happened.
            result.add("seerr", FAILED,
                       f"'{item.title}' has no TMDB id - the Seerr request could not be looked up")
        else:
            request = connectors.seerr.find_request_for_media(item.tmdb_id, item.media_type)
            if not request:
                result.add("seerr", SKIPPED, None)
            else:
                ok = connectors.seerr.delete_request(request["id"])
                result.seerr_deleted = bool(ok)
                result.add("seerr", DONE if ok else FAILED,
                           None if ok else "Seerr request deletion failed")

    return result


def plan_item(item, options: Options, connectors: Connectors) -> dict:
    """What delete_item would do, touching nothing.

    Read calls only: the arr history for the infohashes and the torrent
    listing. Nothing is deleted and no item leaves the library.
    """
    plan = {"id": item.id, "title": item.title, "warnings": [], "actions": []}

    if options.touches_arr:
        quoi = "and its files" if options.files else "but keep its files"
        if item.media_type == "series" and item.sonarr_id and connectors.sonarr:
            plan["actions"].append(f"Sonarr: delete series {item.sonarr_id} {quoi}")
        elif item.media_type == "movie" and item.radarr_id and connectors.radarr:
            plan["actions"].append(f"Radarr: delete movie {item.radarr_id} {quoi}")
        else:
            plan["warnings"].append(arr_skip_reason(item, connectors))

    if options.from_media_server:
        if connectors.media_server is None:
            plan["warnings"].append(f"{connectors.media_server_name} is not configured")
        else:
            plan["actions"].append(
                f"{connectors.media_server_name}: delete entry {item.source_id}"
            )

    if options.torrent:
        if connectors.qbit is None:
            plan["warnings"].append("qBittorrent is not configured")
        else:
            torrents, lookup, ids = find_torrents(item, connectors)
            if torrents:
                noms = ", ".join(t["name"] for t in torrents[:3])
                suite = f" (+{len(torrents) - 3} more)" if len(torrents) > 3 else ""
                plan["actions"].append(
                    f"qBittorrent: remove {len(torrents)} torrent(s) found by {lookup} - {noms}{suite}"
                )
                if lookup == "title":
                    plan["warnings"].append(
                        "Torrents matched by title, which is unreliable - check the names above"
                    )
            elif lookup == "infohash":
                # Pas un avertissement : c'est une reponse rassurante, et la
                # faire apparaitre comme un risque decocherait la ligne dans
                # l'ecran de revue pour rien.
                plan["actions"].append(nothing_left_message(item, ids))
            else:
                plan["warnings"].append(no_infohash_message(item))

    if options.seerr_request and not item.tmdb_id:
        plan["warnings"].append(f"'{item.title}' has no TMDB id - no Seerr request to look up")

    plan["would_free_bytes"] = item.file_size_bytes if options.removes_files else 0
    return plan


def forget_missing_files(media_server) -> list:
    """Make the media server drop entries whose files are gone.

    Whatever removed the files, the media server keeps its entries until it
    looks again. Asking it to rescan needs no write access, unlike deleting
    through it.
    """
    if media_server is None or not hasattr(media_server, "forget_missing_files"):
        return []
    errors = media_server.forget_missing_files()
    for message in errors:
        logger.warning(message)
    return errors
