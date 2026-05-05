"""Tests for promptseal.canonical — canonical JSON serialization for signing.

Spec (BRIEF §5, §13):
- sort_keys=True, separators=(',',':'), ensure_ascii=False
- Stable bytes for any equivalent dict (key order doesn't matter)
- UTF-8 round-trips without escaping non-ASCII
- sha256 hex of canonical bytes is reproducible
"""
from __future__ import annotations

import hashlib
import json

from promptseal.canonical import canonical_json, canonical_sha256


def test_canonical_sorts_keys():
    a = {"b": 1, "a": 2, "c": 3}
    b = {"c": 3, "a": 2, "b": 1}
    assert canonical_json(a) == canonical_json(b)
    assert canonical_json(a) == b'{"a":2,"b":1,"c":3}'


def test_canonical_compact_separators():
    out = canonical_json({"x": 1, "y": [1, 2, 3]})
    # No whitespace between separators
    assert b": " not in out
    assert b", " not in out
    assert out == b'{"x":1,"y":[1,2,3]}'


def test_canonical_preserves_non_ascii():
    # ensure_ascii=False — UTF-8 bytes, not \uXXXX escapes
    out = canonical_json({"name": "Évä", "emoji": "🔒"})
    assert "Évä".encode("utf-8") in out
    assert "🔒".encode("utf-8") in out
    assert b"\\u" not in out


def test_canonical_nested_dicts_sorted_recursively():
    nested = {"outer": {"z": 1, "a": 2}, "abc": 3}
    out = canonical_json(nested)
    # Both top-level and inner keys sorted
    assert out == b'{"abc":3,"outer":{"a":2,"z":1}}'


def test_canonical_returns_bytes():
    out = canonical_json({"a": 1})
    assert isinstance(out, bytes)


def test_canonical_sha256_matches_hashlib():
    payload = {"foo": "bar", "n": 42}
    out = canonical_json(payload)
    expected = hashlib.sha256(out).hexdigest()
    assert canonical_sha256(payload) == expected


def test_canonical_sha256_format_is_hex_64():
    digest = canonical_sha256({"a": 1})
    assert len(digest) == 64
    int(digest, 16)  # parses as hex


def test_canonical_stable_across_runs():
    """Same dict → same bytes every call. No randomness."""
    payload = {"k": "v", "list": [3, 1, 2], "nested": {"b": 2, "a": 1}}
    runs = [canonical_json(payload) for _ in range(5)]
    assert len(set(runs)) == 1


def test_canonical_matches_js_json_stringify_with_sorted_keys():
    """Cross-language smoke: bytes match what JS would produce with sorted keys.

    JS (verifier): JSON.stringify(obj, Object.keys(obj).sort()) over a flat dict.
    For our canonical bytes to verify in browser, the byte sequence must match.
    """
    payload = {"b": 2, "a": 1}
    out = canonical_json(payload)
    # Equivalent JS: JSON.stringify({a:1,b:2}) === '{"a":1,"b":2}'
    js_equivalent = json.dumps({"a": 1, "b": 2}, separators=(",", ":")).encode("utf-8")
    assert out == js_equivalent
