"""Symmetric encryption for secrets held in the database.

Carrier API keys, gateway secrets and webhook signing keys were previously
stored in clear, so a database dump handed over every third-party credential
the company holds. These helpers keep them encrypted at rest.

The key comes from ``FIELD_ENCRYPTION_KEY`` (a urlsafe base64 Fernet key). If
that is unset the key is derived from ``SECRET_KEY`` so development and tests
work without extra configuration; ``validate_production`` requires the real
variable before the app will boot in production.

Rotation: set ``FIELD_ENCRYPTION_KEYS`` to a comma-separated list, newest
first. Values are written with the first key and read with any of them, so a
rotation can run while old rows are re-encrypted in the background.
"""

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

_PREFIX = "enc:v1:"


def _derive(material):
    """Turn arbitrary secret material into a Fernet key."""
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _keys():
    configured = os.getenv("FIELD_ENCRYPTION_KEYS") or os.getenv("FIELD_ENCRYPTION_KEY")
    if configured:
        return [k.strip().encode() for k in configured.split(",") if k.strip()]
    fallback = os.getenv("SECRET_KEY") or "development-only-field-key"
    return [_derive(fallback)]


_cipher = None


def cipher():
    """Build the cipher once per process."""
    global _cipher
    if _cipher is None:
        _cipher = MultiFernet([Fernet(key) for key in _keys()])
    return _cipher


def reset_cipher():
    """Drop the cached cipher. Used by tests that change the key."""
    global _cipher
    _cipher = None


def encrypt(value):
    """Encrypt a string. ``None`` and empty values pass through untouched."""
    if value is None or value == "":
        return value
    if is_encrypted(value):
        return value
    token = cipher().encrypt(str(value).encode("utf-8")).decode("ascii")
    return _PREFIX + token


def decrypt(value):
    """Decrypt a value written by :func:`encrypt`.

    Values stored before encryption was introduced are returned as-is, so the
    switch does not need a migration pass before the app can read its own rows.
    """
    if value is None or value == "":
        return value
    if not is_encrypted(value):
        return value
    try:
        return cipher().decrypt(value[len(_PREFIX):].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None


def is_encrypted(value):
    return isinstance(value, str) and value.startswith(_PREFIX)


def mask(value, keep=4):
    """Render a secret for display: last few characters only."""
    plain = decrypt(value)
    if not plain:
        return ""
    if len(plain) <= keep:
        return "*" * len(plain)
    return "*" * (len(plain) - keep) + plain[-keep:]


class EncryptedField:
    """Descriptor pairing a plaintext attribute with an encrypted column.

    Declaring ``api_key = EncryptedField('api_key_encrypted')`` lets model code
    read and write ``instance.api_key`` while only ciphertext reaches the
    column.
    """

    def __init__(self, column_name):
        self.column_name = column_name

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        return decrypt(getattr(instance, self.column_name))

    def __set__(self, instance, value):
        setattr(instance, self.column_name, encrypt(value))
