// Bridge between the React/TS dashboard and the vanilla verifier modules.
//
// D3: the .js files at ../../verifier/ stay where they are. This module
// re-exports them with explicit TS types, so callers in B2-B5 import from
// `@/lib/cryptoBridge` instead of dotted paths.
//
// B1 only validates that the imports resolve — nothing here is invoked yet.

import * as canonical from "../../../verifier/canonical.js";
import * as verify from "../../../verifier/verify.js";

export const {
  HASH_PREFIX,
  KEY_PREFIX,
  canonicalize,
  canonicalSha256Hex,
  parseJsonPreservingNumbers,
  stripHashPrefix,
  stripKeyPrefix,
  strippedBody,
  hexToBytes,
  bytesToHex,
  base64ToBytes,
} = canonical;

export const { walkMerkleProof, fetchAnchorTxInputData, verifyAll } = verify;

// --- type surface for the rest of the dashboard (B2 will populate) -------

export interface MerkleProofStep {
  side: "L" | "R";
  sibling: string;
}

export interface VerifyAllArgs {
  receiptText: string;
  proofText: string;
  txHash: string;
}

export interface VerifyAllResult {
  ok: boolean;
  step: number;
  message: string;
  [k: string]: unknown;
}

export interface Receipt {
  id?: number;
  agent_id: string;
  agent_erc8004_token_id: number | null;
  event_type: string;
  event_hash: string;
  parent_hash: string | null;
  paired_event_hash: string | null;
  payload_excerpt: Record<string, unknown>;
  public_key: string;
  signature: string;
  schema_version: string;
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
  anchor: { tx_hash: string; block_number: number; chain_id: number };
  proofs: Record<number, MerkleProofStep[]>;
  summary?: RunSummary;
}
