"""Where the token signing key comes from, and what happens when it cannot be found.

The order matters operationally: a key handed over as an environment variable is
printed by any deployment tool that renders the compose file, so the file form
has to win when both are present. And a SECRET_KEY_FILE that cannot be read has
to be fatal, because quietly generating one instead would sign every restart
with a different key and look like a session bug rather than a missing file.
"""

import re

import pytest

from app.config import _read_secret


def test_file_wins_over_environment(tmp_path, monkeypatch):
    chemin = tmp_path / "secret_key"
    chemin.write_text("depuis-le-fichier")
    monkeypatch.setenv("SECRET_KEY_FILE", str(chemin))
    monkeypatch.setenv("SECRET_KEY", "depuis-l-environnement")
    assert _read_secret() == "depuis-le-fichier"


def test_file_content_is_stripped(tmp_path, monkeypatch):
    chemin = tmp_path / "secret_key"
    chemin.write_text("  valeur-entouree-d-espaces\n\n")
    monkeypatch.setenv("SECRET_KEY_FILE", str(chemin))
    monkeypatch.delenv("SECRET_KEY", raising=False)
    assert _read_secret() == "valeur-entouree-d-espaces"


def test_missing_file_is_fatal(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY_FILE", str(tmp_path / "absent"))
    # Even with a usable environment variable: a path that was asked for and
    # cannot be read is a deployment fault, not something to work around.
    monkeypatch.setenv("SECRET_KEY", "depuis-l-environnement")
    with pytest.raises(RuntimeError, match="unreadable"):
        _read_secret()


def test_empty_file_is_fatal(tmp_path, monkeypatch):
    chemin = tmp_path / "secret_key"
    chemin.write_text("   \n")
    monkeypatch.setenv("SECRET_KEY_FILE", str(chemin))
    monkeypatch.delenv("SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="empty file"):
        _read_secret()


def test_environment_is_used_when_no_file_is_asked_for(monkeypatch):
    monkeypatch.delenv("SECRET_KEY_FILE", raising=False)
    monkeypatch.setenv("SECRET_KEY", "depuis-l-environnement")
    assert _read_secret() == "depuis-l-environnement"


def test_blank_values_fall_through(monkeypatch):
    # An empty variable is what a compose file produces for an unset value: it
    # must behave as absent rather than as an empty key.
    monkeypatch.setenv("SECRET_KEY_FILE", "   ")
    monkeypatch.setenv("SECRET_KEY", "")
    assert re.fullmatch(r"[0-9a-f]{64}", _read_secret())


def test_a_key_is_generated_as_a_last_resort(monkeypatch):
    monkeypatch.delenv("SECRET_KEY_FILE", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    genere = _read_secret()
    assert re.fullmatch(r"[0-9a-f]{64}", genere)
    # The generated key is published so anything re-reading the environment in
    # the same process agrees with it, which is the pre-existing behaviour.
    import os

    assert os.environ["SECRET_KEY"] == genere
