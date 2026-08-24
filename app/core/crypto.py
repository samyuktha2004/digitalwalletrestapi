"""AES-256-GCM payload encryption.

Wire format: "<base64(nonce)>:<base64(ciphertext||tag)>" — the spec's
"<iv>:<encrypted_data>" shape, but GCM instead of CBC so a tampered payload is
rejected by the tag rather than silently decrypted into garbage.
"""

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_BYTES = 12  # GCM standard; anything else costs an extra GHASH pass


class DecryptionError(ValueError):
    """Payload was malformed, tampered with, or encrypted under another key."""


def encrypt(plaintext: str, key: bytes) -> str:
    nonce = os.urandom(NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode(), None)
    return f"{base64.b64encode(nonce).decode()}:{base64.b64encode(ciphertext).decode()}"


def decrypt(payload: str, key: bytes) -> str:
    try:
        nonce_b64, ciphertext_b64 = payload.split(":", 1)
        nonce = base64.b64decode(nonce_b64, validate=True)
        ciphertext = base64.b64decode(ciphertext_b64, validate=True)
    except (ValueError, TypeError) as exc:
        raise DecryptionError("malformed payload") from exc

    if len(nonce) != NONCE_BYTES:
        raise DecryptionError("bad nonce length")

    try:
        return AESGCM(key).decrypt(nonce, ciphertext, None).decode()
    except (InvalidTag, UnicodeDecodeError) as exc:
        raise DecryptionError("payload failed authentication") from exc
