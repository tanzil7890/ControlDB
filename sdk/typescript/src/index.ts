/**
 * @controldb/sdk — public API surface.
 *
 * Re-exports every public symbol so callers can import from the single
 * "@controldb/sdk" entry-point.
 */

// Core types and constants
export {
  SCHEMA_VERSION,
  PayloadMode,
  EVENT_TYPES,
  utcNowIso,
} from "./events.js";
export type { Actor, EventEnvelope } from "./events.js";

// Hashing utilities
export { canonicalJson, computeEventHash, computePayloadHash, sha256Hex } from "./hashing.js";

// ID generation
export {
  generateEventId,
  generateRunId,
  generateStepId,
  generateApprovalId,
  generateExportId,
  generateCorrelationId,
  nowIso,
} from "./ids.js";

// Errors
export {
  ControlDBError,
  AuthenticationError,
  AuthorizationError,
  ValidationError,
  RedactionError,
  PolicyViolationError,
  PolicyDeniedError,
  ApprovalRequiredError,
  CollectorUnavailableError,
  IngestError,
  PayloadTooLargeError,
  IdempotencyConflictError,
} from "./errors.js";

// Redaction
export { applyRedaction, REDACTED_SENTINEL } from "./redaction.js";
export type { RedactionRule } from "./redaction.js";

// Transport
export { Transport, Spool } from "./transport.js";
export type { TransportOptions } from "./transport.js";

// Run
export { Run, ToolCallContext } from "./run.js";

// Client (named + default)
export { ControlDB, ControlDB as default } from "./client.js";
export type { ControlDBOptions } from "./client.js";
