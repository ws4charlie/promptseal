"""Tests for promptseal.crypto — Ed25519 keygen, sign, verify, raw encoding.

Spec (BRIEF §13):
- Generate Ed25519 keypair
- Public key serializes to raw 32 bytes (Encoding.Raw / PublicFormat.Raw)
- Private key serializes to raw 32 bytes (the seed)
- sign(message) → 64-byte signature
- verify(public_key_bytes, message, signature) → bool
- Wrong key / wrong message / tampered signature → verify returns False
- Cross-language: Python sign → @noble/ed25519 verify in Node subprocess
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from promptseal.crypto import (
    generate_keypair,
    load_private_key_pem,
    private_key_to_pem,
    public_key_bytes,
    secret_key_bytes,
    sign,
    verify,
)


def test_keypair_generation_returns_object_with_public_key():
    sk = generate_keypair()
    pk_bytes = public_key_bytes(sk)
    assert isinstance(pk_bytes, bytes)
    assert len(pk_bytes) == 32  # @noble/ed25519 raw format


def test_secret_key_serializes_to_32_bytes():
    sk = generate_keypair()
    seed = secret_key_bytes(sk)
    assert isinstance(seed, bytes)
    assert len(seed) == 32


def test_sign_returns_64_byte_signature():
    sk = generate_keypair()
    sig = sign(sk, b"hello world")
    assert isinstance(sig, bytes)
    assert len(sig) == 64


def test_verify_round_trip():
    sk = generate_keypair()
    pk = public_key_bytes(sk)
    msg = b"PromptSeal receipt"
    sig = sign(sk, msg)
    assert verify(pk, msg, sig) is True


def test_verify_wrong_message_returns_false():
    sk = generate_keypair()
    pk = public_key_bytes(sk)
    sig = sign(sk, b"original")
    assert verify(pk, b"tampered", sig) is False


def test_verify_wrong_key_returns_false():
    sk1 = generate_keypair()
    sk2 = generate_keypair()
    msg = b"data"
    sig = sign(sk1, msg)
    assert verify(public_key_bytes(sk2), msg, sig) is False


def test_verify_tampered_signature_returns_false():
    sk = generate_keypair()
    pk = public_key_bytes(sk)
    msg = b"important"
    sig = bytearray(sign(sk, msg))
    sig[0] ^= 0xFF  # flip bits in first byte
    assert verify(pk, msg, bytes(sig)) is False


def test_verify_wrong_signature_length_returns_false():
    sk = generate_keypair()
    pk = public_key_bytes(sk)
    assert verify(pk, b"x", b"too_short") is False


def test_pem_round_trip(tmp_path: Path):
    """Persist + reload should preserve signing capability."""
    sk = generate_keypair()
    pk = public_key_bytes(sk)
    pem_path = tmp_path / "key.pem"
    pem_path.write_bytes(private_key_to_pem(sk))

    sk_loaded = load_private_key_pem(pem_path.read_bytes())
    assert public_key_bytes(sk_loaded) == pk

    sig = sign(sk_loaded, b"after reload")
    assert verify(pk, b"after reload", sig) is True


def test_signature_is_deterministic():
    """Ed25519 signatures are deterministic (RFC 8032). Same key + msg → same sig."""
    sk = generate_keypair()
    msg = b"determinism check"
    assert sign(sk, msg) == sign(sk, msg)


# ---------------------------------------------------------------------------
# Cross-language test: Python sign → JS @noble/ed25519 verify (Node subprocess)
# ---------------------------------------------------------------------------

NODE_AVAILABLE = shutil.which("node") is not None


@pytest.mark.skipif(not NODE_AVAILABLE, reason="node not installed; needed for cross-lang verify")
def test_python_sign_verifies_in_js_noble_ed25519(tmp_path: Path):
    """Critical test for milestone 6: bytes signed in Python must verify in browser.

    We invoke node with @noble/ed25519 (the same library the verifier UI uses)
    and confirm it accepts our signature. If this fails we have a key-encoding
    or message-encoding mismatch — the demo verifier WILL go RED on real receipts.
    """
    sk = generate_keypair()
    pk = public_key_bytes(sk)
    message = b"PromptSeal cross-language verify"
    sig = sign(sk, message)

    payload = {
        "publicKeyHex": pk.hex(),
        "messageHex": message.hex(),
        "signatureHex": sig.hex(),
    }
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps(payload))

    # Inline Node script: dynamic-import @noble/ed25519 from CDN-equivalent npm
    # spec, do verifyAsync, exit 0 if valid else 1.
    node_script = r"""
import('@noble/ed25519').then(async (mod) => {
  const fs = await import('node:fs');
  const ed = mod;
  const data = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
  const pk = Uint8Array.from(Buffer.from(data.publicKeyHex, 'hex'));
  const msg = Uint8Array.from(Buffer.from(data.messageHex, 'hex'));
  const sig = Uint8Array.from(Buffer.from(data.signatureHex, 'hex'));
  const ok = await ed.verifyAsync(sig, msg, pk);
  if (ok) { console.log('VERIFIED'); process.exit(0); }
  else { console.log('REJECTED'); process.exit(1); }
}).catch(e => { console.error(e); process.exit(2); });
"""
    script_path = tmp_path / "verify.mjs"
    script_path.write_text(node_script)

    # Install @noble/ed25519@2.1.0 (same version as verifier CDN import) into
    # tmp_path/node_modules. We pin to match BRIEF §3.
    npm_init = subprocess.run(
        ["npm", "init", "-y"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert npm_init.returncode == 0, f"npm init failed: {npm_init.stderr}"

    npm_install = subprocess.run(
        ["npm", "install", "--silent", "@noble/ed25519@2.1.0"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert npm_install.returncode == 0, f"npm install failed: {npm_install.stderr}"

    result = subprocess.run(
        ["node", str(script_path), str(payload_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"@noble/ed25519 rejected our signature.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "VERIFIED" in result.stdout
