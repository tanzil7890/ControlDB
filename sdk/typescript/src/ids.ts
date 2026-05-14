/**
 * ULID-based ID generation for event_id, run_id, step_id, etc.
 *
 * Uses a Crockford base-32 alphabet (same as the Python SDK) so IDs produced
 * by both SDKs have the same format: prefix_ULID (26 chars).
 *
 * Mirrors sdk/python/controldb/ids.py exactly.
 */

import { randomBytes } from "node:crypto";

const ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";

function ulid(): string {
  // 48-bit millisecond timestamp
  const ms = Date.now() & 0xffffffffffff; // keeps lower 48 bits (wraps ~year 10889)
  // 80-bit random component
  const rand = randomBytes(10);

  // Encode: 48-bit time prefix (10 chars) + 80-bit random suffix (16 chars) = 26 chars
  // Build as a BigInt to avoid 53-bit float precision issues
  const timeBig = BigInt(ms);
  let randBig = BigInt(0);
  for (const byte of rand) {
    randBig = (randBig << BigInt(8)) | BigInt(byte);
  }
  let value = (timeBig << BigInt(80)) | randBig;

  const chars: string[] = [];
  for (let i = 0; i < 26; i++) {
    chars.push(ALPHABET[Number(value & BigInt(0x1f))]);
    value >>= BigInt(5);
  }
  return chars.reverse().join("");
}

function generateId(prefix: string): string {
  return `${prefix}_${ulid()}`;
}

export function generateEventId(): string {
  return generateId("evt");
}

export function generateRunId(): string {
  return generateId("run");
}

export function generateStepId(): string {
  return generateId("step");
}

export function generateApprovalId(): string {
  return generateId("appr");
}

export function generateExportId(): string {
  return generateId("exp");
}

export function generateCorrelationId(): string {
  return randomBytes(16).toString("hex");
}

export function nowIso(): string {
  return new Date().toISOString();
}
