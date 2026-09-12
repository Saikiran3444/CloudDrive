"""Dependency-free password and token helpers."""

import base64
import hashlib
import hmac
import secrets

ITERATIONS = 310_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, iterations, salt_text, digest_text = encoded.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt_text), int(iterations))
        return hmac.compare_digest(actual, base64.b64decode(digest_text))
    except (TypeError, ValueError):
        return False


def create_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Create a fixed-length lookup value without storing a reset token itself."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
