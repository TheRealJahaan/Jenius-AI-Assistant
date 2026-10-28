"""Passwords and tokens.

Passwords are hashed with Argon2id (OWASP's first choice). Session and reset tokens are 256-bit
random strings; only their SHA-256 hash is stored, so a database leak can't be replayed.
"""
import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()  # argon2id, 64 MiB, 3 iterations: argon2-cffi's RFC 9106 low-memory profile

# A real hash of a random password, checked when an email doesn't exist so a failed login takes the
# same time either way and doesn't reveal which emails have accounts.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128
COMMON_PASSWORDS = {
    "password", "password1", "password123", "12345678", "123456789", "1234567890", "qwerty123",
    "qwertyuiop", "iloveyou", "11111111", "00000000", "abc12345", "letmein1", "welcome1", "admin123",
}


def password_problem(password: str, email: str = "") -> str | None:
    """Why a password can't be used, or None if it's fine."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Use at least {MIN_PASSWORD_LENGTH} characters."
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"Use at most {MAX_PASSWORD_LENGTH} characters."
    if password.lower() in COMMON_PASSWORDS:
        return "That password is too common. Pick something harder to guess."
    if email and password.lower() == email.lower():
        return "Your password can't be your email address."
    return None


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
