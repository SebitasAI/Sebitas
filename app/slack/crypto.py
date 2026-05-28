"""Per-workspace bot token encryption (Fernet, key in Doppler).

Why: with multi-tenant install we store one `xoxb-` token per workspace in
Postgres. If a DB dump leaks, those tokens are direct keys to every customer's
Slack. Wrapping them with Fernet means an attacker also needs
`WORKSPACE_TOKEN_ENCRYPTION_KEY` (Doppler-scoped) to do anything with them.

Fernet (AES-128-CBC + HMAC-SHA256) is overkill for the threat model but is
the canonical symmetric scheme in the Python crypto ecosystem and ships with
`cryptography`. No bespoke crypto.

Key rotation is NOT supported in this slice. If the key ever needs rotation,
add a `prev_key` column + lazy re-encrypt on read; out of scope today.
"""

from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


class TokenCryptoError(Exception):
    """Raised when the encryption key is missing/invalid or a token can't be
    decrypted (key rotated, DB corrupted, etc.). The CLI / lookup paths
    surface this as a clean error rather than letting it leak as a stack."""


@lru_cache(maxsize=1)
def _cipher() -> Fernet:
    key = (get_settings().workspace_token_encryption_key or "").strip()
    if not key:
        raise TokenCryptoError(
            "WORKSPACE_TOKEN_ENCRYPTION_KEY not set; generate one with "
            "`python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`"
        )
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise TokenCryptoError(f"invalid Fernet key in Doppler: {exc}") from exc


def encrypt_token(plaintext: str) -> str:
    """Returns the Fernet-encrypted ciphertext as a base64 str. Idempotent on
    empty input (returns empty string)."""
    if not plaintext:
        return ""
    return _cipher().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    """Inverse of encrypt_token. Raises TokenCryptoError on bad ciphertext."""
    if not ciphertext:
        return ""
    try:
        return _cipher().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise TokenCryptoError(
            "couldn't decrypt token (key mismatch or corrupted ciphertext)"
        ) from exc
