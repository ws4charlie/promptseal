// Step-wise verifier for the dashboard's EventDetailPanel.
//
// verify.js exports a monolithic verifyAll(...) plus two of the 5 steps as
// individual helpers (walkMerkleProof, fetchAnchorTxInputData). To render
// per-step progress in React (one tick per step, ✓/✗ as each completes),
// we reuse those two exports + the canonical.js primitives, and implement
// steps 1/2/5 here. verifier/verify.js is NOT modified (D3).
//
// @noble/ed25519 is the npm-pinned 2.1.0 — same library the CDN URL inside
// verify.js loads, just bundled instead of fetched. Adopting it costs zero
// extra deps (already in package.json since B1).

import * as ed from "@noble/ed25519";

import {
  HASH_PREFIX,
  TEXT_ENC,
  base64ToBytes,
  canonicalSha256Hex,
  canonicalize,
  stripHashPrefix,
  stripKeyPrefix,
  strippedBody,
} from "../../../verifier/canonical.js";
import {
  fetchAnchorTxInputData,
  walkMerkleProof,
} from "../../../verifier/verify.js";

import type {
  EvidencePack,
  MerkleProofStep,
  Receipt,
} from "./evidencePack";

// ---------------------------------------------------------------------------
// per-step result types

export type StepStatus = "pending" | "running" | "ok" | "fail";

export interface StepResult {
  status: StepStatus;
  message?: string;
  detail?: string;
}

export interface VerifyState {
  steps: [StepResult, StepResult, StepResult, StepResult, StepResult];
  // Once all 5 finish ok, this is set.
  done: boolean;
  // First failing step index (1-based) or null while running / on success.
  firstFail: number | null;
}

export const STEP_LABELS: readonly string[] = [
  "recompute event_hash from canonical body",
  "verify Ed25519 signature",
  "walk Merkle proof to a root",
  "fetch anchor TX input data",
  "compare reconstructed root to on-chain root",
];

export function emptyVerifyState(): VerifyState {
  const pending: StepResult = { status: "pending" };
  return {
    steps: [pending, pending, pending, pending, pending],
    done: false,
    firstFail: null,
  };
}

// ---------------------------------------------------------------------------
// body shaping for steps 1 + 2

function bodyForVerification(receipt: Receipt): Record<string, unknown> {
  // Strip the dashboard-side `id` field before delegating to
  // verifier/canonical.js's strippedBody. The signed body never contained
  // `id` — that field is added by the evidence pack export (B2) for proofs
  // lookup, not present at signing time. Without this, recomputed
  // event_hash diverges from stored.
  const rest = { ...(receipt as unknown as Record<string, unknown>) };
  delete rest.id;
  return strippedBody(rest) as Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// individual steps (each returns a StepResult; never throws on validation
// failure — only throws on truly unexpected programmer errors)

async function step1RecomputeEventHash(receipt: Receipt): Promise<StepResult> {
  try {
    const body = bodyForVerification(receipt);
    const hexDigest = await canonicalSha256Hex(body);
    const recomputed = HASH_PREFIX + hexDigest;
    if (recomputed !== receipt.event_hash) {
      return {
        status: "fail",
        message:
          "event_hash mismatch — receipt body has been tampered.",
        detail: `recomputed ${recomputed}\nstored     ${receipt.event_hash}`,
      };
    }
    return {
      status: "ok",
      message: "matches stored event_hash",
      detail: receipt.event_hash,
    };
  } catch (err) {
    return {
      status: "fail",
      message: "recompute threw",
      detail: err instanceof Error ? err.message : String(err),
    };
  }
}

async function step2VerifySignature(receipt: Receipt): Promise<StepResult> {
  try {
    const body = bodyForVerification(receipt);
    const canonicalStr = canonicalize(body);
    const canonicalBytes = TEXT_ENC.encode(canonicalStr);
    const pkBytes = base64ToBytes(stripKeyPrefix(receipt.public_key));
    const sigBytes = base64ToBytes(stripKeyPrefix(receipt.signature));
    const ok = await ed.verifyAsync(sigBytes, canonicalBytes, pkBytes);
    if (!ok) {
      return {
        status: "fail",
        message: "signature does NOT verify against public_key",
        detail:
          `pubkey ${receipt.public_key.slice(0, 32)}…\n` +
          `sig    ${receipt.signature.slice(0, 32)}…`,
      };
    }
    return {
      status: "ok",
      message: "Ed25519 signature valid",
      detail: receipt.public_key,
    };
  } catch (err) {
    return {
      status: "fail",
      message: "signature verify threw",
      detail: err instanceof Error ? err.message : String(err),
    };
  }
}

async function step3WalkProof(
  receipt: Receipt,
  proof: MerkleProofStep[],
): Promise<{ result: StepResult; reconstructedHex: string | null }> {
  try {
    const leafHex = stripHashPrefix(receipt.event_hash);
    const reconstructedHex = await walkMerkleProof(leafHex, proof);
    return {
      result: {
        status: "ok",
        message: `walked ${proof.length}-step proof`,
        detail: HASH_PREFIX + reconstructedHex,
      },
      reconstructedHex,
    };
  } catch (err) {
    return {
      result: {
        status: "fail",
        message: "merkle proof walk threw",
        detail: err instanceof Error ? err.message : String(err),
      },
      reconstructedHex: null,
    };
  }
}

async function step4FetchAnchorRoot(
  txHash: string,
): Promise<{ result: StepResult; onChainHex: string | null }> {
  try {
    const inputHex = await fetchAnchorTxInputData(txHash);
    if (inputHex.length !== 64) {
      return {
        result: {
          status: "fail",
          message: `tx.input is ${inputHex.length / 2} bytes, expected 32`,
          detail: `tx_hash ${txHash}`,
        },
        onChainHex: null,
      };
    }
    return {
      result: {
        status: "ok",
        message: "anchor TX found",
        detail: HASH_PREFIX + inputHex,
      },
      onChainHex: inputHex,
    };
  } catch (err) {
    return {
      result: {
        status: "fail",
        message: "anchor tx fetch failed",
        detail: err instanceof Error ? err.message : String(err),
      },
      onChainHex: null,
    };
  }
}

function step5CompareRoots(
  reconstructedHex: string,
  onChainHex: string,
): StepResult {
  if (reconstructedHex.toLowerCase() !== onChainHex.toLowerCase()) {
    return {
      status: "fail",
      message: "merkle root from proof ≠ on-chain anchor root",
      detail:
        `proof→${HASH_PREFIX}${reconstructedHex}\n` +
        `chain→${HASH_PREFIX}${onChainHex}`,
    };
  }
  return {
    status: "ok",
    message: "reconstructed root === on-chain anchor root",
    detail: HASH_PREFIX + reconstructedHex,
  };
}

// ---------------------------------------------------------------------------
// orchestrator — runs the 5 steps sequentially, emits progress callbacks

export interface VerifyOrchestratorArgs {
  receipt: Receipt;
  proof: MerkleProofStep[];
  txHash: string;
  onUpdate: (state: VerifyState) => void;
  // Optional cached on-chain anchor root (hex, no "sha256:" prefix). When
  // provided, step 4 skips the live RPC fetch and reuses this value. Lets
  // RunPage verify N receipts with 1 RPC call total instead of N. The
  // cached value still flows through step 5's comparison.
  cachedAnchorRootHex?: string;
}

export async function verifyEventStepwise(
  args: VerifyOrchestratorArgs,
): Promise<VerifyState> {
  const { receipt, proof, txHash, onUpdate, cachedAnchorRootHex } = args;
  const state = emptyVerifyState();

  const setStep = (i: 0 | 1 | 2 | 3 | 4, r: StepResult) => {
    state.steps[i] = r;
    if (r.status === "fail" && state.firstFail === null) {
      state.firstFail = i + 1;
    }
    onUpdate({ ...state, steps: [...state.steps] as VerifyState["steps"] });
  };

  // Step 1
  setStep(0, { status: "running" });
  const s1 = await step1RecomputeEventHash(receipt);
  setStep(0, s1);
  if (s1.status === "fail") return state;

  // Step 2
  setStep(1, { status: "running" });
  const s2 = await step2VerifySignature(receipt);
  setStep(1, s2);
  if (s2.status === "fail") return state;

  // Step 3
  setStep(2, { status: "running" });
  const s3 = await step3WalkProof(receipt, proof);
  setStep(2, s3.result);
  if (s3.result.status === "fail" || s3.reconstructedHex === null) return state;

  // Step 4 — skip the RPC fetch if a cached root was passed in.
  setStep(3, { status: "running" });
  let onChainHex: string;
  if (cachedAnchorRootHex !== undefined) {
    onChainHex = cachedAnchorRootHex;
    setStep(3, {
      status: "ok",
      message: "anchor TX root (cached, fetched once for the run)",
      detail: HASH_PREFIX + onChainHex,
    });
  } else {
    const s4 = await step4FetchAnchorRoot(txHash);
    setStep(3, s4.result);
    if (s4.result.status === "fail" || s4.onChainHex === null) return state;
    onChainHex = s4.onChainHex;
  }

  // Step 5
  setStep(4, { status: "running" });
  const s5 = step5CompareRoots(s3.reconstructedHex, onChainHex);
  setStep(4, s5);
  if (s5.status === "fail") return state;

  state.done = true;
  onUpdate({ ...state, steps: [...state.steps] as VerifyState["steps"] });
  return state;
}

// ---------------------------------------------------------------------------
// one-shot anchor root fetch — for callers that want to verify N receipts
// without paying for N RPC fetches. Returns the on-chain root as hex (no
// "sha256:" prefix) or throws on RPC failure / unexpected tx shape.

export async function fetchAnchorRootOnce(txHash: string): Promise<string> {
  const inputHex = await fetchAnchorTxInputData(txHash);
  if (inputHex.length !== 64) {
    throw new Error(
      `tx.input is ${inputHex.length / 2} bytes, expected 32 (a SHA-256 root). ` +
        `Wrong tx? Tx hash: ${txHash}`,
    );
  }
  return inputHex;
}

// ---------------------------------------------------------------------------
// convenience: pull the right inputs out of an evidence pack for a receipt id

export interface VerifyInputs {
  receipt: Receipt;
  proof: MerkleProofStep[];
  txHash: string;
}

export function pickVerifyInputs(
  pack: EvidencePack,
  receiptId: number,
): VerifyInputs | null {
  const receipt = pack.receipts.find((r) => r.id === receiptId);
  if (!receipt) return null;
  const proof = pack.proofs[String(receiptId)];
  if (!proof) return null;
  return { receipt, proof, txHash: pack.anchor.tx_hash };
}
