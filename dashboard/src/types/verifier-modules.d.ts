// Module shims for the vanilla verifier (verifier/canonical.js, verify.js)
// and the CDN URL specifier inside verify.js.
//
// The .js files stay where they are (D3) — these declarations let TypeScript
// understand the imports without rewriting the verifier in TS.

declare module "*verifier/canonical.js" {
  export const HASH_PREFIX: string;
  export const KEY_PREFIX: string;
  export const TEXT_ENC: TextEncoder;

  export function stripHashPrefix(s: string): string;
  export function stripKeyPrefix(s: string): string;
  export function hexToBytes(hex: string): Uint8Array;
  export function bytesToHex(bytes: Uint8Array): string;
  export function base64ToBytes(b64: string): Uint8Array;

  export class NumberToken {
    constructor(raw: string);
    raw: string;
  }

  export function parseJsonPreservingNumbers(text: string): unknown;
  export function canonicalize(value: unknown): Uint8Array;
  export function canonicalSha256Hex(value: unknown): Promise<string>;
  export function strippedBody(
    receipt: Record<string, unknown>,
  ): Record<string, unknown>;
}

declare module "*verifier/verify.js" {
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

  export function walkMerkleProof(
    leafHashHex: string,
    proof: MerkleProofStep[],
  ): Promise<string>;

  export function fetchAnchorTxInputData(
    txHash: string,
    rpcUrl?: string,
  ): Promise<string>;

  export function verifyAll(args: VerifyAllArgs): Promise<VerifyAllResult>;
}

// verify.js imports @noble/ed25519 from a jsdelivr URL. Type it as opaque —
// only verify.js touches it; the React side never imports the URL directly.
declare module "https://cdn.jsdelivr.net/npm/@noble/ed25519@2.1.0/+esm" {
  const lib: unknown;
  export = lib;
}
