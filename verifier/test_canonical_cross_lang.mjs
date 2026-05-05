// Cross-language byte-equality test for the canonical JSON layer.
//
// Run:  node verifier/test_canonical_cross_lang.mjs
//
// Loads two real receipts (one with float `temperature: 0.0`, one with all
// strings/null/int) saved by Python from promptseal.sqlite, re-canonicalizes
// them in JS via verifier/canonical.js, hashes, and asserts the result
// equals the Python-computed event_hash that's stored alongside.
//
// If this test fails: do NOT push the verifier. The browser GREEN ✓ would be
// a lie — Python and JS bytes have diverged.

import { readFileSync } from "node:fs";
import {
  HASH_PREFIX,
  canonicalize,
  canonicalSha256Hex,
  parseJsonPreservingNumbers,
  strippedBody,
} from "./canonical.js";

const fixtures = JSON.parse(
  readFileSync(new URL("./test_fixtures.json", import.meta.url), "utf8"),
);

let passed = 0;
let failed = 0;

for (const fx of fixtures) {
  // Fixtures store the receipt as Python's canonical_json output (raw text)
  // so float source forms like "0.0" survive. Parsing that text with our
  // number-preserving parser mirrors what the browser does when a user
  // pastes JSON.
  const parsed = parseJsonPreservingNumbers(fx.receipt_canonical_text);
  const body = strippedBody(parsed);
  const canon = canonicalize(body);
  const hexDigest = await canonicalSha256Hex(body);
  const recomputed = HASH_PREFIX + hexDigest;

  const ok = recomputed === fx.expected_event_hash;
  console.log(
    `${ok ? "PASS" : "FAIL"}  id=${fx.id}  type=${fx.event_type}  ` +
    `len=${canon.length}B`,
  );
  if (!ok) {
    console.log(`        expected ${fx.expected_event_hash}`);
    console.log(`        got      ${recomputed}`);
    console.log(`        canonical bytes (first 200): ${canon.slice(0, 200)}…`);
    failed++;
  } else {
    console.log(`        canonical preview: ${canon.slice(0, 80)}…`);
    console.log(`        sha256 = ${recomputed}`);
    passed++;
  }
}

console.log("");
console.log(`canonical cross-lang test: ${passed} passed, ${failed} failed`);
process.exit(failed === 0 ? 0 : 1);
