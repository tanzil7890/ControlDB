/**
 * Event envelope model and validated constants.
 * Mirrors sdk/python/controldb/events.py exactly.
 */

export const SCHEMA_VERSION = "2026-05-01";

export enum PayloadMode {
  METADATA_ONLY = "metadata_only",
  HASH_ONLY = "hash_only",
  REDACTED_PAYLOAD = "redacted_payload",
  FULL_PAYLOAD = "full_payload",
  SELF_HOSTED_PAYLOAD = "self_hosted_payload",
}

/**
 * Canonical event type catalog — 38 types.
 * Mirrored from specs/event-schema/v1/event_types.json.
 */
export const EVENT_TYPES: ReadonlySet<string> = new Set([
  "agent_run.started",
  "agent_run.committed",
  "agent_run.rolled_back",
  "agent_run.failed",
  "agent_run.requires_approval",
  "model_call.started",
  "model_call.completed",
  "model_call.failed",
  "tool_call.started",
  "tool_call.completed",
  "tool_call.failed",
  "memory.read",
  "memory.write",
  "memory.delete_requested",
  "memory.redacted",
  "state.snapshot.created",
  "state.change.proposed",
  "state.change.applied",
  "state.change.reverted",
  "policy.check.started",
  "policy.check.passed",
  "policy.check.failed",
  "policy.enforcement.blocked",
  "policy.enforcement.allowed",
  "approval.requested",
  "approval.approved",
  "approval.rejected",
  "approval.expired",
  "approval.overridden",
  "evidence.artifact.created",
  "evidence.export.started",
  "evidence.export.completed",
  "evidence.export.failed",
  "auth.api_key.created",
  "auth.api_key.revoked",
  "user.login",
  "user.permission.changed",
  "audit_log.exported",
]);

export function utcNowIso(): string {
  return new Date().toISOString();
}

export interface Actor {
  type: string;
  id: string;
}

export interface EventEnvelope {
  event_id: string;
  schema_version: string;
  event_type: string;
  org_id: string;
  project_id: string;
  environment: string;
  agent_id: string;
  run_id: string;
  actor: Actor;
  payload_mode: string;
  event_hash: string;
  occurred_at: string;
  ingested_at: string;
  agent_version?: string | null;
  step_id?: string | null;
  step_index?: number | null;
  trace_id?: string | null;
  span_id?: string | null;
  parent_span_id?: string | null;
  payload?: Record<string, unknown> | null;
  payload_hash?: string | null;
  previous_event_hash?: string | null;
  idempotency_key?: string | null;
}
