// Evidence pack types + loaders. Mirrors PLAN §7 schema and the JSON shape
// produced by scripts/04_export_evidence_pack.py.
//
// Three loaders cover the three entry points:
//   - URL              → ?evidence=https://… or pasted into the landing page
//   - drag-drop ZIP    → contains evidence-pack.json
//   - URL param        → reads ?evidence= on page load
//
// We intentionally avoid zod/yup — a hand-rolled validator keeps deps thin
// (D8 spirit) and fails loudly on the few keys we actually depend on.
//
// Numbers are loaded via parseJsonPreservingNumbers so that float source
// representation ("0.0" stays "0.0") survives into verification. That means
// every numeric value in the wire JSON arrives as a NumberToken instance,
// not a JS number primitive. The validator normalizes top-level/metadata
// numbers back to plain numbers so the rest of the dashboard can use them
// directly; payload_excerpt is left untouched (its NumberTokens are what
// canonicalize() needs to byte-equal Python's signed bytes).

import JSZip from "jszip";

import {
  NumberToken,
  parseJsonPreservingNumbers,
} from "../../../verifier/canonical.js";

// --- canonical types (match scripts/04_export_evidence_pack.py output) ---

export interface MerkleProofStep {
  side: "L" | "R";
  sibling: string;
}

export interface AnchorTx {
  tx_hash: string;
  block_number: number;
  chain_id: number;
}

export interface Receipt {
  id: number;
  agent_id: string;
  agent_erc8004_token_id: number | null;
  event_type: string;
  event_hash: string;
  parent_hash: string | null;
  paired_event_hash: string | null;
  payload_excerpt: Record<string, unknown>;
  public_key: string;
  schema_version: string;
  signature: string;
  timestamp: string;
}

export interface RunSummary {
  text: string;
  hash: string;
  generated_at: string;
  llm_provider: string;
  llm_model: string;
  included_in_merkle: boolean;
}

export interface EvidencePack {
  version: "0.2";
  agent_id: string;
  agent_erc8004_token_id: number | null;
  run_id: string;
  receipts: Receipt[];
  merkle_root: string;
  anchor: AnchorTx;
  // JSON object keys are strings; receipt id is the integer-as-string key.
  proofs: Record<string, MerkleProofStep[]>;
  summary?: RunSummary;
  // Tamper-feature snapshot (Q-tamper): deep clone of `receipts` taken at
  // load time. Mutations to `receipts[N].payload_excerpt` (via the Edit /
  // Tamper UI in EventDetailPanel) are detected by comparing against this
  // snapshot, and Restore copies the original payload back. Preserves
  // NumberToken instances so canonical bytes round-trip exactly. Underscore
  // prefix marks it as runtime-only state — never serialized to disk.
  __originalReceipts?: Receipt[];
}

// --- validator -------------------------------------------------------------

export class EvidencePackValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "EvidencePackValidationError";
  }
}

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v) && !(v instanceof NumberToken);
}

function asNumber(v: unknown): number | null {
  // parseJsonPreservingNumbers wraps every JSON number as NumberToken. Top-
  // level metadata fields (block_number, chain_id, ids) need to be plain JS
  // numbers for display + lookup; this helper does that conversion.
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (v instanceof NumberToken) {
    const n = Number(v.src);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

function requireString(obj: Record<string, unknown>, key: string): string {
  const v = obj[key];
  if (typeof v !== "string") {
    throw new EvidencePackValidationError(`field '${key}' must be string, got ${typeof v}`);
  }
  return v;
}

function requireNumber(obj: Record<string, unknown>, key: string): number {
  const n = asNumber(obj[key]);
  if (n === null) {
    throw new EvidencePackValidationError(
      `field '${key}' must be finite number, got ${typeof obj[key]}`,
    );
  }
  return n;
}

function requireIntOrNull(obj: Record<string, unknown>, key: string): number | null {
  const v = obj[key];
  if (v === null) return null;
  const n = asNumber(v);
  if (n === null || !Number.isInteger(n)) {
    throw new EvidencePackValidationError(`field '${key}' must be integer or null`);
  }
  return n;
}

function validateAnchor(v: unknown): AnchorTx {
  if (!isObject(v)) {
    throw new EvidencePackValidationError("'anchor' must be object");
  }
  return {
    tx_hash: requireString(v, "tx_hash"),
    block_number: requireNumber(v, "block_number"),
    chain_id: requireNumber(v, "chain_id"),
  };
}

function validateReceipt(v: unknown, ix: number): Receipt {
  if (!isObject(v)) {
    throw new EvidencePackValidationError(`receipts[${ix}] must be object`);
  }
  // We don't deep-validate every receipt field — verify.js / inlineVerifier
  // will re-derive event_hash + signature themselves. Just check the keys
  // the dashboard routes on, and normalize their NumberTokens to numbers.
  const id = asNumber(v.id);
  if (id === null || !Number.isInteger(id)) {
    throw new EvidencePackValidationError(`receipts[${ix}].id must be integer`);
  }
  // Return a shallow copy with normalized id + token id. payload_excerpt is
  // copied by reference — its NumberTokens are needed by verification.
  const out: Record<string, unknown> = { ...v, id };
  if (v.agent_erc8004_token_id !== null) {
    const tid = asNumber(v.agent_erc8004_token_id);
    if (tid !== null) out.agent_erc8004_token_id = tid;
  }
  return out as unknown as Receipt;
}

function validateProofs(v: unknown): Record<string, MerkleProofStep[]> {
  if (!isObject(v)) {
    throw new EvidencePackValidationError("'proofs' must be object");
  }
  for (const [k, steps] of Object.entries(v)) {
    if (!Array.isArray(steps)) {
      throw new EvidencePackValidationError(`proofs['${k}'] must be array`);
    }
    steps.forEach((step, i) => {
      if (!isObject(step) || (step.side !== "L" && step.side !== "R") ||
          typeof step.sibling !== "string") {
        throw new EvidencePackValidationError(
          `proofs['${k}'][${i}] malformed (need {side: 'L'|'R', sibling: string})`,
        );
      }
    });
  }
  return v as Record<string, MerkleProofStep[]>;
}

function validateSummary(v: unknown): RunSummary {
  if (!isObject(v)) {
    throw new EvidencePackValidationError("'summary' must be object when present");
  }
  return {
    text: requireString(v, "text"),
    hash: requireString(v, "hash"),
    generated_at: requireString(v, "generated_at"),
    llm_provider: requireString(v, "llm_provider"),
    llm_model: requireString(v, "llm_model"),
    included_in_merkle: typeof v.included_in_merkle === "boolean"
      ? v.included_in_merkle
      : (() => {
          throw new EvidencePackValidationError(
            "summary.included_in_merkle must be boolean",
          );
        })(),
  };
}

export function validateEvidencePack(data: unknown): EvidencePack {
  if (!isObject(data)) {
    throw new EvidencePackValidationError("evidence pack must be a JSON object");
  }
  if (data.version !== "0.2") {
    throw new EvidencePackValidationError(
      `unsupported version: ${JSON.stringify(data.version)} (expected '0.2')`,
    );
  }
  if (!Array.isArray(data.receipts)) {
    throw new EvidencePackValidationError("'receipts' must be array");
  }
  const receipts = data.receipts.map(validateReceipt);

  const pack: EvidencePack = {
    version: "0.2",
    agent_id: requireString(data, "agent_id"),
    agent_erc8004_token_id: requireIntOrNull(data, "agent_erc8004_token_id"),
    run_id: requireString(data, "run_id"),
    receipts,
    merkle_root: requireString(data, "merkle_root"),
    anchor: validateAnchor(data.anchor),
    proofs: validateProofs(data.proofs),
  };

  if (data.summary !== undefined) {
    pack.summary = validateSummary(data.summary);
  }

  // Snapshot a deep-cloned copy of receipts for Tamper detection + Restore.
  // structuredClone would lose NumberToken class identity (it copies the
  // src field but as a plain object), which would corrupt verify on
  // restore — Receipt.payload_excerpt contains NumberToken instances that
  // canonical_json() must round-trip byte-equal to Python's signed bytes.
  // Hence the bespoke clone helper.
  pack.__originalReceipts = pack.receipts.map(deepCloneReceipt);

  return pack;
}

// Bespoke deep clone that preserves NumberToken class identity. Plain
// objects, arrays, primitives, and null are handled normally; NumberToken
// instances are reconstructed via `new NumberToken(src)` so canonical
// round-tripping after Restore continues to match Python's signed bytes.
function deepCloneReceipt(r: Receipt): Receipt {
  return cloneValue(r) as Receipt;
}

function cloneValue(v: unknown): unknown {
  if (v === null || typeof v !== "object") return v;
  if (v instanceof NumberToken) return new NumberToken(v.src);
  if (Array.isArray(v)) return v.map(cloneValue);
  const out: Record<string, unknown> = {};
  for (const [k, val] of Object.entries(v as Record<string, unknown>)) {
    out[k] = cloneValue(val);
  }
  return out;
}

// --- tamper detection -----------------------------------------------------
//
// JSON.stringify-based deep equality is sufficient because:
//   1. Both current and original may contain NumberToken instances (which
//      stringify as `{"src":"..."}`). Same source → same string.
//   2. After user Apply (JSON.parse from textarea), current loses
//      NumberTokens — stringify produces `0` instead of `{"src":"0.0"}`.
//      Different from original → detected as tampered. Correct.
//   3. After user Restore (clone from __originalReceipts), the cloned
//      payload has NumberTokens again — matches original byte-for-byte.

function jsonOf(v: unknown): string {
  return JSON.stringify(v);
}

export function isReceiptTampered(pack: EvidencePack, index: number): boolean {
  const orig = pack.__originalReceipts?.[index];
  const curr = pack.receipts[index];
  if (!orig || !curr) return false;
  return jsonOf(curr.payload_excerpt) !== jsonOf(orig.payload_excerpt);
}

// Find receipt by id and check if tampered. Receipt id is stable across the
// pack's lifetime; this is what UI components have access to (node.start.id,
// node.end.id) without knowing the array index.
export function isReceiptIdTampered(
  pack: EvidencePack,
  receiptId: number | undefined,
): boolean {
  if (receiptId === undefined) return false;
  const idx = pack.receipts.findIndex((r) => r.id === receiptId);
  return idx >= 0 && isReceiptTampered(pack, idx);
}

export function hasAnyTamper(pack: EvidencePack): boolean {
  if (!pack.__originalReceipts) return false;
  return pack.receipts.some((_, i) => isReceiptTampered(pack, i));
}

// Restore one receipt's payload from the original snapshot. Caller is
// responsible for triggering re-render + re-verify; this just mutates pack.
export function restoreReceiptPayload(pack: EvidencePack, index: number): void {
  const orig = pack.__originalReceipts?.[index];
  const curr = pack.receipts[index];
  if (!orig || !curr) return;
  curr.payload_excerpt = cloneValue(orig.payload_excerpt) as Record<
    string,
    unknown
  >;
}

// --- loaders ---------------------------------------------------------------

export async function loadFromURL(url: string): Promise<EvidencePack> {
  let res: Response;
  try {
    res = await fetch(url, { headers: { Accept: "application/json" } });
  } catch (err) {
    throw new EvidencePackValidationError(
      `fetch failed for ${url}: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
  if (!res.ok) {
    throw new EvidencePackValidationError(
      `fetch ${url} returned HTTP ${res.status}`,
    );
  }
  let parsed: unknown;
  try {
    // Use the source-preserving parser so float receipts (e.g. temperature:
    // 0.0) stay byte-equal to what Python signed.
    const text = await res.text();
    parsed = parseJsonPreservingNumbers(text);
  } catch (err) {
    throw new EvidencePackValidationError(
      `response from ${url} was not valid JSON: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
  return validateEvidencePack(parsed);
}

export async function loadFromZip(file: File): Promise<EvidencePack> {
  let zip: JSZip;
  try {
    zip = await JSZip.loadAsync(file);
  } catch (err) {
    throw new EvidencePackValidationError(
      `not a valid ZIP: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
  const entry = zip.file("evidence-pack.json");
  if (entry === null) {
    throw new EvidencePackValidationError(
      "ZIP does not contain evidence-pack.json (case-sensitive)",
    );
  }
  const text = await entry.async("string");
  let parsed: unknown;
  try {
    parsed = parseJsonPreservingNumbers(text);
  } catch (err) {
    throw new EvidencePackValidationError(
      `evidence-pack.json inside ZIP is not valid JSON: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
  return validateEvidencePack(parsed);
}

export async function loadFromUrlParam(): Promise<EvidencePack | null> {
  if (typeof window === "undefined") return null;
  const url = new URLSearchParams(window.location.search).get("evidence");
  if (!url) return null;
  return loadFromURL(url);
}

// Self-contained HTML mode (D7). build_self_contained.py injects:
//   <script>window.__PROMPTSEAL_EVIDENCE__ = "<base64-of-pack-json>";</script>
// before the bundled JS executes. We decode + validate synchronously; this
// is the fastest path because there's no network or zip parsing.
export function loadFromEmbedded(): EvidencePack | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as { __PROMPTSEAL_EVIDENCE__?: string };
  const b64 = w.__PROMPTSEAL_EVIDENCE__;
  if (typeof b64 !== "string" || b64.length === 0) return null;
  let json: string;
  try {
    json = atob(b64);
  } catch (err) {
    throw new EvidencePackValidationError(
      `embedded __PROMPTSEAL_EVIDENCE__ is not valid base64: ${
        err instanceof Error ? err.message : String(err)
      }`,
    );
  }
  let parsed: unknown;
  try {
    parsed = parseJsonPreservingNumbers(json);
  } catch (err) {
    throw new EvidencePackValidationError(
      `embedded evidence is not valid JSON: ${
        err instanceof Error ? err.message : String(err)
      }`,
    );
  }
  return validateEvidencePack(parsed);
}
