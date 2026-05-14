/**
 * Canonical JSON + hash-chain utilities.
 *
 * The same canonicalization rules must be used by SDK and collector to keep
 * hash chains verifiable across both sides.
 *
 * Mirrors sdk/python/controldb/hashing.py exactly.
 */

import { createHash } from "node:crypto";

/**
 * Deterministic JSON encoding used as input to event hashing.
 * Keys are sorted recursively, whitespace stripped, producing identical output
 * to Python's json.dumps(value, sort_keys=True, separators=(",", ":")).
 */
export function canonicalJson(obj: unknown): string {
  if (obj === null || obj === undefined) {
    return "null";
  }
  if (typeof obj === "boolean") {
    return obj ? "true" : "false";
  }
  if (typeof obj === "number") {
    if (!isFinite(obj)) {
      throw new TypeError("NaN and Infinity are not allowed in canonical JSON");
    }
    return JSON.stringify(obj);
  }
  if (typeof obj === "string") {
    return JSON.stringify(obj);
  }
  if (Array.isArray(obj)) {
    const items = obj.map((item) => canonicalJson(item));
    return "[" + items.join(",") + "]";
  }
  if (typeof obj === "object") {
    const record = obj as Record<string, unknown>;
    const sortedKeys = Object.keys(record).sort();
    const pairs = sortedKeys.map(
      (key) => JSON.stringify(key) + ":" + canonicalJson(record[key])
    );
    return "{" + pairs.join(",") + "}";
  }
  // Fallback for any other type (bigint, symbol, function) — match Python repr behaviour
  return JSON.stringify(String(obj));
}

export function sha256Hex(data: string): string {
  return "sha256:" + createHash("sha256").update(data, "utf8").digest("hex");
}

export function computePayloadHash(payload: unknown): string {
  const effective = payload !== null && payload !== undefined ? payload : {};
  return sha256Hex(canonicalJson(effective));
}

/**
 * Hash an event envelope excluding the event_hash field.
 *
 *   event_hash = sha256(canonical_json(envelope_without_event_hash))
 *
 * Because previous_event_hash lives inside the envelope, the hash chain is
 * already linked when we exclude only event_hash itself.
 */
export function computeEventHash(
  envelope: Omit<Record<string, unknown>, "event_hash"> & { event_hash?: string }
): string {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const { event_hash: _discarded, ...rest } = envelope as Record<string, unknown>;
  return sha256Hex(canonicalJson(rest));
}
