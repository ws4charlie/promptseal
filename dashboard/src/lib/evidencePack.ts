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

import JSZip from "jszip";

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
}

// --- validator -------------------------------------------------------------

export class EvidencePackValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "EvidencePackValidationError";
  }
}

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function requireString(obj: Record<string, unknown>, key: string): string {
  const v = obj[key];
  if (typeof v !== "string") {
    throw new EvidencePackValidationError(`field '${key}' must be string, got ${typeof v}`);
  }
  return v;
}

function requireNumber(obj: Record<string, unknown>, key: string): number {
  const v = obj[key];
  if (typeof v !== "number" || !Number.isFinite(v)) {
    throw new EvidencePackValidationError(`field '${key}' must be finite number, got ${typeof v}`);
  }
  return v;
}

function requireIntOrNull(obj: Record<string, unknown>, key: string): number | null {
  const v = obj[key];
  if (v === null) return null;
  if (typeof v !== "number" || !Number.isInteger(v)) {
    throw new EvidencePackValidationError(`field '${key}' must be integer or null`);
  }
  return v;
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
  // We don't deep-validate every receipt field — verifier/verify.js will
  // re-derive event_hash + signature itself. Just check the keys we route on.
  const id = requireNumber(v, "id");
  if (!Number.isInteger(id)) {
    throw new EvidencePackValidationError(`receipts[${ix}].id must be integer`);
  }
  return v as unknown as Receipt;
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
  return pack;
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
    parsed = await res.json();
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
    parsed = JSON.parse(text);
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
