"""Canonical JSON serialization for PromptSeal receipts.

Every receipt is hashed and signed over canonical bytes. The browser verifier
(JS @noble/ed25519) must produce identical bytes from the same dict, so this
module is the single source of truth for serialization rules:

  - sort_keys=True     : nested dicts sorted recursively
  - separators=(',',':'): no whitespace
  - ensure_ascii=False : UTF-8, not \\uXXXX escapes
  - encoding: utf-8 bytes (not str)

Pitfall guarded against (BRIEF §13): the #1 source of "verifies in Python but
not in browser" bugs is forgetting one of these flags.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(obj: Any) -> bytes:
    """Serialize *obj* to canonical JSON bytes (sorted keys, compact, UTF-8)."""
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def canonical_sha256(obj: Any) -> str:
    """Return hex sha256 digest of canonical JSON bytes for *obj*."""
    return hashlib.sha256(canonical_json(obj)).hexdigest()
