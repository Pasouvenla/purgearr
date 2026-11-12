import os
import secrets

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:////data/purgearr.db")


def _read_secret() -> str:
    """Return the token signing key, preferring a file over an environment variable.

    A deployment tool that renders the compose file resolves environment
    variables and prints the result, so a key passed that way ends up in that
    tool's logs for anyone who can read them. A path does not, which is why
    SECRET_KEY_FILE is preferred when both are set.
    """
    path = os.environ.get("SECRET_KEY_FILE", "").strip()
    if path:
        # Deliberately fatal, with no fallback. Generating a key instead would
        # sign every restart with a different one, logging everybody out while
        # looking like a session expiry bug rather than a missing file.
        try:
            with open(path, encoding="utf-8") as handle:
                value = handle.read().strip()
        except OSError as error:
            raise RuntimeError(f"SECRET_KEY_FILE is set but unreadable: {path} ({error})") from error
        if not value:
            raise RuntimeError(f"SECRET_KEY_FILE points at an empty file: {path}")
        return value

    value = os.environ.get("SECRET_KEY", "").strip()
    if value:
        return value

    value = secrets.token_hex(32)
    os.environ["SECRET_KEY"] = value
    return value


SECRET_KEY = _read_secret()

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 7
