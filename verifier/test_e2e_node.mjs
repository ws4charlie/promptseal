// End-to-end flow exercised via Node — same canonicalize, hash, merkle walk,
// and RPC fetch the browser does, minus only the Ed25519 signature verify
// (which lives behind the @noble/ed25519 CDN URL in verify.js). Catches every
// failure mode that doesn't require the noble lib.

import { readFileSync } from "node:fs";
import { createPublicKey, verify as nodeVerify } from "node:crypto";
import {
  HASH_PREFIX,
  bytesToHex,
  base64ToBytes,
  canonicalize,
  canonicalSha256Hex,
  hexToBytes,
  parseJsonPreservingNumbers,
  stripHashPrefix,
  stripKeyPrefix,
  strippedBody,
} from "./canonical.js";

// Verify Ed25519 via Node's built-in crypto. Mathematically equivalent to
// what @noble/ed25519 does in the browser — both are deterministic over the
// canonical bytes, so identical input bytes give identical verify result.
function verifyEd25519Node(rawPubkey32, message, signature64) {
  // Build a SubjectPublicKeyInfo DER for the raw 32-byte Ed25519 pubkey.
  // SPKI prefix for Ed25519 is fixed: 12 bytes.
  const spkiPrefix = Buffer.from("302a300506032b6570032100", "hex");
  const der = Buffer.concat([spkiPrefix, Buffer.from(rawPubkey32)]);
  const pubKey = createPublicKey({ key: der, format: "der", type: "spki" });
  return nodeVerify(null, Buffer.from(message), pubKey, Buffer.from(signature64));
}

async function sha256Bytes(bytes) {
  const d = await crypto.subtle.digest("SHA-256", bytes);
  return new Uint8Array(d);
}

async function walkMerkleProof(leafHashHex, proof) {
  let cur = hexToBytes(leafHashHex);
  for (const step of proof) {
    const sib = hexToBytes(stripHashPrefix(step.sibling));
    const combined = new Uint8Array(64);
    if (step.side === "R") { combined.set(cur, 0); combined.set(sib, 32); }
    else                   { combined.set(sib, 0); combined.set(cur, 32); }
    cur = await sha256Bytes(combined);
  }
  return bytesToHex(cur);
}

async function fetchAnchorRoot(txHash, rpcUrl) {
  const res = await fetch(rpcUrl, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      jsonrpc: "2.0", method: "eth_getTransactionByHash", params: [txHash], id: 1,
    }),
  });
  const json = await res.json();
  const data = json.result.input;
  return data.slice(2);
}

const SAMPLE = JSON.parse(readFileSync(new URL("./e2e_sample.json", import.meta.url), "utf8"));

const receipt = parseJsonPreservingNumbers(SAMPLE.receipt_canonical_text);

// Step 1: recompute event_hash
const recomputed = HASH_PREFIX + (await canonicalSha256Hex(strippedBody(receipt)));
const step1 = recomputed === receipt.event_hash;
console.log(`step 1 (event_hash recompute): ${step1 ? "PASS" : "FAIL"}`);
console.log(`  recomputed: ${recomputed}`);
console.log(`  stored    : ${receipt.event_hash}`);

// Step 2: Ed25519 signature verify (Node's built-in matches noble)
const canonicalBytes = new TextEncoder().encode(canonicalize(strippedBody(receipt)));
const pkBytes = base64ToBytes(stripKeyPrefix(receipt.public_key));
const sigBytes = base64ToBytes(stripKeyPrefix(receipt.signature));
const step2 = verifyEd25519Node(pkBytes, canonicalBytes, sigBytes);
console.log(`step 2 (Ed25519 signature):    ${step2 ? "PASS" : "FAIL"}`);
console.log(`  pubkey 32B?: ${pkBytes.length === 32}, sig 64B?: ${sigBytes.length === 64}`);

// Step 3: walk merkle proof
const reconstructed = await walkMerkleProof(stripHashPrefix(receipt.event_hash), SAMPLE.proof);
const step3 = HASH_PREFIX + reconstructed === SAMPLE.expected_root;
console.log(`step 3 (merkle proof walk):    ${step3 ? "PASS" : "FAIL"}`);
console.log(`  reconstructed: ${HASH_PREFIX}${reconstructed}`);
console.log(`  expected root: ${SAMPLE.expected_root}`);

// Step 4 + 5: fetch on-chain root + compare
const onChain = await fetchAnchorRoot(SAMPLE.tx_hash, "https://sepolia.base.org");
const step5 = onChain.toLowerCase() === reconstructed.toLowerCase();
console.log(`step 4+5 (RPC + chain match):  ${step5 ? "PASS" : "FAIL"}`);
console.log(`  on-chain root: ${HASH_PREFIX}${onChain}`);

const allOk = step1 && step2 && step3 && step5;
console.log("");
console.log(allOk ? "ALL 5 STEPS PASSED in Node (mirrors browser path byte-for-byte)." : "FAIL");
process.exit(allOk ? 0 : 1);
