"""
Symmetric encryption for sensitive tokens stored in the database.

Uses Fernet (AES-128-CBC + HMAC-SHA256) with a key from the
ELEVATION_ENCRYPTION_KEY environment variable (K8s Secret).

If the key is not configured, operates in pass-through mode so
dev/CI environments work without setting up a Secret.

Backwards-compatible: decrypt_token returns plaintext values as-is
when decryption fails (legacy data stored before encryption was enabled).
"""

import logging
import os

logger = logging.getLogger(__name__)

_ENCRYPTION_KEY = os.getenv("ELEVATION_ENCRYPTION_KEY", "")

_fernet = None
if _ENCRYPTION_KEY:
    try:
        from cryptography.fernet import Fernet

        _fernet = Fernet(_ENCRYPTION_KEY.encode() if isinstance(_ENCRYPTION_KEY, str) else _ENCRYPTION_KEY)
    except Exception as e:
        logger.warning("ELEVATION_ENCRYPTION_KEY is set but invalid: %s. Tokens will NOT be encrypted.", e)
else:
    logger.warning(
        "ELEVATION_ENCRYPTION_KEY not set. Tokens will be stored/read as plaintext. "
        "Set it in production via K8s Secret 'elevation-encryption-secret'."
    )


def encrypt_token(plaintext: str) -> str:
    """Encrypt a token value. Returns base64-encoded ciphertext, or plaintext if no key."""
    if not plaintext:
        return plaintext
    if _fernet is None:
        return plaintext
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    """
    Decrypt a token value. Falls back to returning the value as-is if:
    - No encryption key is configured
    - The value is legacy plaintext (not a valid Fernet token)
    """
    if not ciphertext:
        return ciphertext
    if _fernet is None:
        return ciphertext
    try:
        return _fernet.decrypt(ciphertext.encode()).decode()
    except Exception:
        # Legacy plaintext value — return as-is
        return ciphertext
