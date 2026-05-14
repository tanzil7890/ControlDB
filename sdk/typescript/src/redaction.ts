/**
 * Payload redaction.
 *
 * Supports:
 * - Field redaction (key match anywhere in nested dicts/arrays)
 * - Regex redaction (string values matching a regex)
 * - REDACT / HASH / DROP modes
 *
 * Mirrors sdk/python/controldb/redaction.py exactly.
 */

import { createHash } from "node:crypto";

export const REDACTED_SENTINEL = "<redacted>";

export interface RedactionRule {
  /** Exact key name to redact anywhere in the payload tree */
  field?: string;
  /** Regex pattern applied to all string values */
  regex?: string;
  /** What to do with matched values */
  mode: "REDACT" | "HASH" | "DROP" | "redact" | "hash" | "drop";
}

/** Normalise mode strings so both "REDACT" and "redact" work */
function normaliseMode(mode: string): "redact" | "hash" | "drop" {
  const lower = mode.toLowerCase() as "redact" | "hash" | "drop";
  if (lower !== "redact" && lower !== "hash" && lower !== "drop") {
    throw new Error(`unknown redaction mode: ${mode}`);
  }
  return lower;
}

function applyMode(value: unknown, mode: "redact" | "hash" | "drop"): unknown {
  if (mode === "drop") {
    return undefined; // caller must delete the key
  }
  if (mode === "hash") {
    const digest = createHash("sha256")
      .update(JSON.stringify(value), "utf8")
      .digest("hex");
    return `sha256:${digest}`;
  }
  return REDACTED_SENTINEL;
}

interface CompiledRule {
  field?: string;
  regex?: RegExp;
  mode: "redact" | "hash" | "drop";
}

function compileRules(rules: RedactionRule[]): CompiledRule[] {
  return rules.map((r) => {
    if (!r.field && !r.regex) {
      throw new Error("RedactionRule requires field or regex");
    }
    return {
      field: r.field,
      regex: r.regex ? new RegExp(r.regex) : undefined,
      mode: normaliseMode(r.mode),
    };
  });
}

function deepCopy(value: unknown): unknown {
  if (value === null || value === undefined) return value;
  if (typeof value !== "object") return value;
  if (Array.isArray(value)) return value.map(deepCopy);
  const copy: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
    copy[k] = deepCopy(v);
  }
  return copy;
}

function redactValue(value: unknown, rules: CompiledRule[]): unknown {
  if (Array.isArray(value)) {
    return value.map((item) => redactValue(item, rules));
  }
  if (value !== null && typeof value === "object") {
    return redactObject(value as Record<string, unknown>, rules);
  }
  if (typeof value === "string") {
    for (const rule of rules) {
      if (rule.regex && rule.regex.test(value)) {
        return applyMode(value, rule.mode);
      }
    }
  }
  return value;
}

function redactField(
  key: string,
  value: unknown,
  rules: CompiledRule[]
): { keep: boolean; value: unknown } {
  for (const rule of rules) {
    if (rule.field && rule.field === key) {
      if (rule.mode === "drop") {
        return { keep: false, value: undefined };
      }
      return { keep: true, value: applyMode(value, rule.mode) };
    }
  }
  return { keep: true, value: redactValue(value, rules) };
}

function redactObject(
  obj: Record<string, unknown>,
  rules: CompiledRule[]
): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(obj)) {
    const { keep, value } = redactField(k, v, rules);
    if (keep) {
      result[k] = value;
    }
  }
  return result;
}

/**
 * Return a redacted deep-copy of the payload.
 * Mirrors Python apply_redaction().
 */
export function applyRedaction(
  payload: unknown,
  rules: RedactionRule[]
): unknown {
  if (payload === null || payload === undefined) {
    return payload;
  }
  const compiledRules = compileRules(rules);
  const data = deepCopy(payload);
  return redactValue(data, compiledRules);
}
