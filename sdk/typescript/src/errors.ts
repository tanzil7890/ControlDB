/**
 * SDK error hierarchy.
 * Mirrors sdk/python/controldb/errors.py exactly.
 */

export class ControlDBError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ControlDBError";
    // Restore prototype chain for instanceof checks in transpiled code
    Object.setPrototypeOf(this, new.target.prototype);
  }
}

export class AuthenticationError extends ControlDBError {
  constructor(message = "unauthenticated") {
    super(message);
    this.name = "AuthenticationError";
    Object.setPrototypeOf(this, new.target.prototype);
  }
}

export class AuthorizationError extends ControlDBError {
  constructor(message = "forbidden") {
    super(message);
    this.name = "AuthorizationError";
    Object.setPrototypeOf(this, new.target.prototype);
  }
}

export class ValidationError extends ControlDBError {
  constructor(message: string) {
    super(message);
    this.name = "ValidationError";
    Object.setPrototypeOf(this, new.target.prototype);
  }
}

export class RedactionError extends ControlDBError {
  constructor(message: string) {
    super(message);
    this.name = "RedactionError";
    Object.setPrototypeOf(this, new.target.prototype);
  }
}

export class PolicyViolationError extends ControlDBError {
  readonly policyId: string;
  readonly reason: string;

  constructor(message: string, options: { policyId?: string; reason?: string } = {}) {
    super(message);
    this.name = "PolicyViolationError";
    this.policyId = options.policyId ?? "";
    this.reason = options.reason ?? "";
    Object.setPrototypeOf(this, new.target.prototype);
  }
}

/** Alias matching Python PolicyDeniedError name */
export { PolicyViolationError as PolicyDeniedError };

export class ApprovalRequiredError extends ControlDBError {
  readonly approvalId: string;
  readonly policyId: string;

  constructor(
    message: string,
    options: { approvalId?: string; policyId?: string } = {}
  ) {
    super(message);
    this.name = "ApprovalRequiredError";
    this.approvalId = options.approvalId ?? "";
    this.policyId = options.policyId ?? "";
    Object.setPrototypeOf(this, new.target.prototype);
  }
}

export class CollectorUnavailableError extends ControlDBError {
  constructor(message = "collector unavailable") {
    super(message);
    this.name = "CollectorUnavailableError";
    Object.setPrototypeOf(this, new.target.prototype);
  }
}

export class IngestError extends ControlDBError {
  constructor(message: string) {
    super(message);
    this.name = "IngestError";
    Object.setPrototypeOf(this, new.target.prototype);
  }
}

export class PayloadTooLargeError extends ControlDBError {
  constructor(message = "payload too large") {
    super(message);
    this.name = "PayloadTooLargeError";
    Object.setPrototypeOf(this, new.target.prototype);
  }
}

export class IdempotencyConflictError extends ControlDBError {
  constructor(message = "conflict") {
    super(message);
    this.name = "IdempotencyConflictError";
    Object.setPrototypeOf(this, new.target.prototype);
  }
}
