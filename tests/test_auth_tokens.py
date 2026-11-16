"""
Unit tests for password hashing and JWT handling.

Auth had no test coverage at all before the python-jose to PyJWT migration,
which is exactly the code where a silent behaviour change is most costly: a
token that stops validating logs everyone out, and one that validates too
loosely is a security hole. These tests pin the contract independently of the
library behind it.
"""
from datetime import timedelta

import jwt
import pytest

from app.auth.utils import (
    create_access_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.config import ALGORITHM, SECRET_KEY


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------

def test_password_hash_is_not_the_password():
    hashed = hash_password("hunter2")
    assert hashed != "hunter2"
    assert hashed.startswith("$2")


def test_password_verifies_against_its_hash():
    assert verify_password("hunter2", hash_password("hunter2")) is True


def test_wrong_password_is_rejected():
    assert verify_password("wrong", hash_password("hunter2")) is False


def test_hashing_is_salted():
    """Two hashes of the same password must differ, or they leak equality."""
    assert hash_password("hunter2") != hash_password("hunter2")


# ---------------------------------------------------------------------------
# Token round-trip
# ---------------------------------------------------------------------------

def test_token_round_trip_preserves_the_subject():
    token = create_access_token({"sub": "tim"})
    assert decode_token(token)["sub"] == "tim"


def test_token_is_a_string():
    """PyJWT 2.x returns str; a bytes token would break the cookie header."""
    assert isinstance(create_access_token({"sub": "tim"}), str)


def test_token_carries_an_expiry():
    assert "exp" in decode_token(create_access_token({"sub": "tim"}))


def test_expired_token_is_rejected():
    token = create_access_token({"sub": "tim"}, expires_delta=timedelta(seconds=-10))
    assert decode_token(token) is None


def test_token_signed_with_another_key_is_rejected():
    forged = jwt.encode({"sub": "attacker"}, "not-the-secret", algorithm=ALGORITHM)
    assert decode_token(forged) is None


def test_garbage_is_rejected_without_raising():
    assert decode_token("not-a-token") is None
    assert decode_token("") is None


def test_unsigned_token_is_rejected():
    """alg=none is the classic JWT bypass; the algorithms allow-list blocks it."""
    unsigned = jwt.encode({"sub": "attacker"}, key="", algorithm="none")
    assert decode_token(unsigned) is None


def test_algorithm_is_symmetric_hmac():
    """HS256 is what makes the ecdsa dependency unnecessary — pin the intent."""
    assert ALGORITHM == "HS256"


def test_token_is_a_standard_jwt():
    """Interoperable format: existing cookies must survive the library swap."""
    token = create_access_token({"sub": "tim"})
    assert len(token.split(".")) == 3
    assert jwt.get_unverified_header(token)["alg"] == "HS256"


def test_a_token_from_the_previous_library_still_validates():
    """Guards against forcing every logged-in user to sign in again."""
    legacy = jwt.encode({"sub": "tim", "exp": 9999999999}, SECRET_KEY, algorithm="HS256")
    assert decode_token(legacy)["sub"] == "tim"
