"""Ed25519 keypair, sign, verify — wrapped over the `cryptography` library.

Keys are exposed as raw 32-byte buffers (RFC 8032 / @noble/ed25519 layout) so
the browser verifier (JS) can consume the same bytes. PEM (PKCS8) is used only
for on-disk persistence.
"""
from __future__ import annotations

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.exceptions import InvalidSignature


class PromptSealSignatureError(Exception):
    """Raised when a signature fails verification at a layer that needs to fail loud."""


def generate_keypair() -> Ed25519PrivateKey:
    """Generate a new Ed25519 private key (public key derivable from it)."""
    return Ed25519PrivateKey.generate()


def public_key_bytes(sk: Ed25519PrivateKey) -> bytes:
    """Return the raw 32-byte public key for *sk* — matches @noble/ed25519."""
    return sk.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def secret_key_bytes(sk: Ed25519PrivateKey) -> bytes:
    """Return the raw 32-byte seed for *sk*."""
    return sk.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )


def private_key_to_pem(sk: Ed25519PrivateKey) -> bytes:
    """Serialize *sk* to unencrypted PKCS8 PEM bytes (for on-disk storage)."""
    return sk.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def load_private_key_pem(pem: bytes) -> Ed25519PrivateKey:
    """Load an Ed25519 private key from unencrypted PKCS8 PEM bytes."""
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("PEM did not contain an Ed25519 private key")
    return key


def sign(sk: Ed25519PrivateKey, message: bytes) -> bytes:
    """Sign *message* with *sk*. Returns 64-byte Ed25519 signature."""
    return sk.sign(message)


def verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """Verify *signature* over *message* under raw 32-byte *public_key*.

    Returns False on any failure (wrong key, tampered sig/msg, malformed input).
    Never raises — callers wanting hard-fail behavior should check and raise
    PromptSealSignatureError themselves.
    """
    if len(public_key) != 32 or len(signature) != 64:
        return False
    try:
        pk = Ed25519PublicKey.from_public_bytes(public_key)
        pk.verify(signature, message)
        return True
    except (InvalidSignature, ValueError):
        return False
