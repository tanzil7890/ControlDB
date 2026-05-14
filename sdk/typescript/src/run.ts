/**
 * High-level Run and ToolCallContext objects exposed to SDK users.
 * Mirrors sdk/python/controldb/run.py exactly.
 */

import { ApprovalRequiredError, PolicyViolationError, ValidationError } from "./errors.js";
import { EVENT_TYPES, EventEnvelope, PayloadMode, SCHEMA_VERSION, Actor, utcNowIso } from "./events.js";
import { computeEventHash, computePayloadHash } from "./hashing.js";
import { generateApprovalId, generateEventId, generateStepId } from "./ids.js";
import { applyRedaction, RedactionRule } from "./redaction.js";
import type { ControlDB } from "./client.js";

// ---- ToolCallContext -------------------------------------------------------

export class ToolCallContext {
  private readonly _run: Run;
  private readonly _name: string;
  private readonly _stepId: string;
  private _input: Record<string, unknown> | null = null;
  private _output: Record<string, unknown> | null = null;
  private _redact: boolean;
  private _started = false;

  constructor(run: Run, name: string, options: { redact?: boolean } = {}) {
    this._run = run;
    this._name = name;
    this._stepId = generateStepId();
    this._redact = options.redact ?? true;
  }

  get stepId(): string {
    return this._stepId;
  }

  /** Record the input data for this tool call */
  input(data: Record<string, unknown>): void {
    this._input = data;
  }

  /** Record the output data for this tool call */
  output(data: Record<string, unknown>, options: { redact?: boolean } = {}): void {
    this._output = data;
    if (options.redact !== undefined) {
      this._redact = options.redact;
    }
  }

  /** Emit tool_call.started. Called automatically when used with startToolCall(). */
  async start(): Promise<void> {
    if (!this._started) {
      await this._run._emit("tool_call.started", {
        name: this._name,
        input: this._input,
      }, { stepId: this._stepId, redact: this._redact });
      this._started = true;
    }
  }

  /** Emit tool_call.completed (or tool_call.failed on error). */
  async finish(error?: Error): Promise<void> {
    if (!this._started) {
      await this.start();
    }
    if (error) {
      await this._run._emit("tool_call.failed", {
        name: this._name,
        input: this._input,
        error: { type: error.name, message: error.message },
      }, { stepId: this._stepId, redact: this._redact });
    } else {
      await this._run._emit("tool_call.completed", {
        name: this._name,
        input: this._input,
        output: this._output,
      }, { stepId: this._stepId, redact: this._redact });
    }
  }

  /** Async dispose — allows use with `await using` (TC39 explicit resource management) */
  async [Symbol.asyncDispose](): Promise<void> {
    await this.finish();
  }
}

// ---- Internal emit options ------------------------------------------------

interface EmitOptions {
  stepId?: string;
  stepIndex?: number;
  actor?: Actor;
  payloadMode?: string;
  redact?: boolean;
}

// ---- Run ------------------------------------------------------------------

export class Run {
  readonly runId: string;
  readonly agentId: string;
  readonly agentVersion: string | undefined;
  readonly environment: string;
  readonly traceId: string | undefined;

  private readonly _client: ControlDB;
  private _prevEventHash: string | null = null;
  private _stepCounter = 0;
  private _buffer: Record<string, unknown>[] = [];
  private _status = "running";
  private _committed = false;
  private _closed = false;

  constructor(
    client: ControlDB,
    runId: string,
    agentId: string,
    agentVersion?: string,
    environment?: string,
    traceId?: string
  ) {
    this._client = client;
    this.runId = runId;
    this.agentId = agentId;
    this.agentVersion = agentVersion;
    this.environment = environment ?? client.environment;
    this.traceId = traceId;
  }

  private _nextStepIndex(): number {
    return this._stepCounter++;
  }

  /** Build, hash, and buffer one event envelope. */
  async _emit(
    eventType: string,
    payload?: Record<string, unknown> | null,
    options: EmitOptions = {}
  ): Promise<Record<string, unknown>> {
    if (!EVENT_TYPES.has(eventType)) {
      throw new ValidationError(`unknown event_type: ${eventType}`);
    }

    const { stepId, actor, redact = true } = options;
    const stepIndex = options.stepIndex !== undefined ? options.stepIndex : this._nextStepIndex();
    const captureMode = options.payloadMode ?? this._client.defaultPayloadMode;

    let preparedPayload: Record<string, unknown> | null = null;
    let payloadHash: string | null = null;

    if (payload !== null && payload !== undefined) {
      const rules: RedactionRule[] = this._client.redactionRules;
      const redacted = (redact && rules.length > 0)
        ? (applyRedaction(payload, rules) as Record<string, unknown>)
        : payload;

      switch (captureMode) {
        case PayloadMode.METADATA_ONLY:
        case "metadata_only":
          preparedPayload = null;
          payloadHash = computePayloadHash(redacted);
          break;
        case PayloadMode.HASH_ONLY:
        case "hash_only":
          preparedPayload = null;
          payloadHash = computePayloadHash(redacted);
          break;
        case PayloadMode.REDACTED_PAYLOAD:
        case "redacted_payload":
          preparedPayload = typeof redacted === "object" && !Array.isArray(redacted)
            ? redacted as Record<string, unknown>
            : { value: redacted };
          payloadHash = computePayloadHash(redacted);
          break;
        case PayloadMode.FULL_PAYLOAD:
        case "full_payload":
          preparedPayload = typeof payload === "object" && !Array.isArray(payload)
            ? payload
            : { value: payload };
          payloadHash = computePayloadHash(payload);
          break;
        case PayloadMode.SELF_HOSTED_PAYLOAD:
        case "self_hosted_payload":
          preparedPayload = {
            artifact_uri: (payload as Record<string, unknown>)["artifact_uri"] ?? null,
          };
          payloadHash = computePayloadHash(payload);
          break;
        default:
          preparedPayload = preparedPayload;
          payloadHash = computePayloadHash(redacted);
      }
    }

    const effectiveActor: Actor = actor ?? { type: "agent", id: this.agentId };

    const envelopeData: Record<string, unknown> = {
      event_id: generateEventId(),
      schema_version: SCHEMA_VERSION,
      event_type: eventType,
      org_id: this._client.orgId,
      project_id: this._client.project,
      environment: this.environment,
      agent_id: this.agentId,
      agent_version: this.agentVersion ?? null,
      run_id: this.runId,
      step_id: stepId ?? null,
      step_index: stepIndex,
      trace_id: this.traceId ?? null,
      span_id: null,
      parent_span_id: null,
      actor: effectiveActor,
      payload_mode: captureMode,
      payload: preparedPayload,
      payload_hash: payloadHash,
      previous_event_hash: this._prevEventHash,
      event_hash: "",
      occurred_at: utcNowIso(),
      ingested_at: utcNowIso(),
    };

    const eventHash = computeEventHash(envelopeData);
    envelopeData["event_hash"] = eventHash;
    this._prevEventHash = eventHash;

    const idempotencyKey = `${this.runId}:${stepIndex}:${eventType}`;
    envelopeData["idempotency_key"] = idempotencyKey;

    this._buffer.push(envelopeData);

    if (this._buffer.length >= this._client.batchSize) {
      await this._flush();
    }

    return envelopeData;
  }

  /** Flush buffered events to the transport. */
  async _flush(): Promise<void> {
    if (this._buffer.length === 0) return;
    const events = [...this._buffer];
    this._buffer = [];
    const firstId = events[0]["event_id"] as string;
    const lastId = events[events.length - 1]["event_id"] as string;
    const idempotencyKey = `${this.runId}:batch:${firstId}:${lastId}`;
    try {
      await this._client.transport.postEvents(
        this.runId,
        events as unknown as EventEnvelope[],
        idempotencyKey
      );
    } catch (err) {
      // Requeue events at the head of the buffer so the next flush retries them
      this._buffer = [...events, ...this._buffer];
      throw err;
    }
  }

  // ---- Public named event helpers -----------------------------------------

  async input(options: {
    userId?: string;
    inputHash?: string;
    metadata?: Record<string, unknown>;
  } = {}): Promise<void> {
    await this._emit("agent_run.started", {
      user_id: options.userId ?? null,
      input_hash: options.inputHash ?? null,
      metadata: options.metadata ?? {},
    });
  }

  /**
   * Returns a ToolCallContext. Caller must call .start() or .finish() manually,
   * or use `await using` (explicit resource management).
   */
  startToolCall(name: string, options: { redact?: boolean } = {}): ToolCallContext {
    return new ToolCallContext(this, name, options);
  }

  /** Convenience wrapper: emits tool_call.started + tool_call.completed */
  async toolCall(
    name: string,
    options: {
      input?: Record<string, unknown>;
      output?: Record<string, unknown>;
      durationMs?: number;
      redact?: boolean;
    } = {}
  ): Promise<void> {
    const stepId = generateStepId();
    const redact = options.redact ?? true;
    await this._emit("tool_call.started", {
      name,
      input: options.input ?? null,
    }, { stepId, redact });
    await this._emit("tool_call.completed", {
      name,
      input: options.input ?? null,
      output: options.output ?? null,
      duration_ms: options.durationMs ?? null,
    }, { stepId, redact });
  }

  async modelCall(options: {
    model: string;
    prompt?: unknown;
    completion?: unknown;
    provider?: string;
    usage?: Record<string, unknown>;
    inputTokens?: number;
    outputTokens?: number;
    durationMs?: number;
    redact?: boolean;
    success?: boolean;
  }): Promise<void> {
    const {
      model,
      prompt = null,
      completion = null,
      provider = null,
      usage,
      inputTokens,
      outputTokens,
      durationMs = null,
      redact = true,
      success = true,
    } = options;

    const resolvedUsage: Record<string, unknown> = usage ?? {};
    if (inputTokens !== undefined) resolvedUsage["input_tokens"] = inputTokens;
    if (outputTokens !== undefined) resolvedUsage["output_tokens"] = outputTokens;

    const payload: Record<string, unknown> = {
      model,
      provider,
      prompt,
      completion,
      usage: resolvedUsage,
      duration_ms: durationMs,
    };
    await this._emit(
      success ? "model_call.completed" : "model_call.failed",
      payload,
      { redact }
    );
  }

  async memoryRead(options: {
    key: string;
    value?: unknown;
    store?: string;
    redact?: boolean;
  }): Promise<void> {
    await this._emit("memory.read", {
      store: options.store ?? "default",
      key: options.key,
      value: options.value ?? null,
    }, { redact: options.redact ?? true });
  }

  async memoryWrite(options: {
    key: string;
    value: unknown;
    store?: string;
    redact?: boolean;
  }): Promise<void> {
    await this._emit("memory.write", {
      store: options.store ?? "default",
      key: options.key,
      value: options.value,
    }, { redact: options.redact ?? true });
  }

  async stateChange(options: {
    entityType: string;
    entityId: string;
    before: Record<string, unknown>;
    after: Record<string, unknown>;
    reason?: string;
    applied?: boolean;
    redact?: boolean;
  }): Promise<void> {
    const eventType = (options.applied ?? true)
      ? "state.change.applied"
      : "state.change.proposed";
    await this._emit(eventType, {
      entity_type: options.entityType,
      entity_id: options.entityId,
      before: options.before,
      after: options.after,
      reason: options.reason ?? null,
    }, { redact: options.redact ?? true });
  }

  async policyCheck(options: {
    policyId: string;
    input: Record<string, unknown>;
    result?: Record<string, unknown>;
    policyVersion?: string;
    policyHash?: string;
  }): Promise<Record<string, unknown>> {
    let { result, policyVersion, policyHash } = options;

    if (!result) {
      const response = await this._client.transport.policyCheck({
        policy_id: options.policyId,
        input: options.input,
        run_id: this.runId,
      });
      result = (response["result"] as Record<string, unknown>) ?? response;
      policyVersion = policyVersion ?? (response["policy_version"] as string | undefined);
      policyHash = policyHash ?? (response["policy_hash"] as string | undefined);
    }

    const passed =
      Boolean(result["allowed"] !== false || result["requires_approval"]);
    const eventType = passed ? "policy.check.passed" : "policy.check.failed";

    await this._emit(eventType, {
      policy_id: options.policyId,
      policy_version: policyVersion ?? null,
      policy_hash: policyHash ?? null,
      input: options.input,
      result,
    });

    return result;
  }

  async enforcePolicy(options: {
    policyId: string;
    input: Record<string, unknown>;
  }): Promise<{ allowed: boolean; requiresApproval?: boolean; reason?: string }> {
    const result = await this.policyCheck({
      policyId: options.policyId,
      input: options.input,
    });

    if (result["allowed"] === false && !result["requires_approval"]) {
      await this._emit("policy.enforcement.blocked", {
        policy_id: options.policyId,
        input: options.input,
        result,
      });
      throw new PolicyViolationError(
        (result["reason"] as string) ?? "policy denied",
        { policyId: options.policyId, reason: (result["reason"] as string) ?? "" }
      );
    }

    if (result["requires_approval"]) {
      const approval = await this.requestApproval({
        policyId: options.policyId,
        input: options.input,
        reason: (result["reason"] as string) ?? "approval required",
      });
      throw new ApprovalRequiredError(
        (result["reason"] as string) ?? "approval required",
        {
          approvalId: (approval["approval_id"] as string) ?? "",
          policyId: options.policyId,
        }
      );
    }

    await this._emit("policy.enforcement.allowed", {
      policy_id: options.policyId,
      input: options.input,
      result,
    });

    return {
      allowed: Boolean(result["allowed"] !== false),
      requiresApproval: Boolean(result["requires_approval"]),
      reason: (result["reason"] as string) ?? undefined,
    };
  }

  async requestApproval(options: {
    approvalId?: string;
    policyId?: string;
    input?: Record<string, unknown>;
    reason?: string;
  }): Promise<Record<string, unknown>> {
    const approvalId = options.approvalId ?? generateApprovalId();
    const body: Record<string, unknown> = {
      approval_id: approvalId,
      run_id: this.runId,
      policy_id: options.policyId ?? null,
      input: options.input ?? null,
      reason: options.reason ?? null,
    };
    await this._emit("approval.requested", body);
    await this._flush(); // Flush so the dashboard can pick up the request
    return this._client.transport.requestApproval(body);
  }

  async humanApproval(options: {
    approvalId: string;
    reviewerId: string;
    decision: "approved" | "rejected" | "expired" | "overridden";
    reason?: string;
  }): Promise<void> {
    const validDecisions = ["approved", "rejected", "expired", "overridden"];
    if (!validDecisions.includes(options.decision)) {
      throw new ValidationError(`invalid approval decision: ${options.decision}`);
    }
    await this._emit(
      `approval.${options.decision}`,
      {
        approval_id: options.approvalId,
        reviewer_id: options.reviewerId,
        decision: options.decision,
        reason: options.reason ?? null,
      },
      { actor: { type: "human", id: options.reviewerId } }
    );
  }

  async evidenceArtifact(options: {
    uri: string;
    sha256: string;
    kind?: string;
    description?: string;
  }): Promise<void> {
    await this._emit("evidence.artifact.created", {
      uri: options.uri,
      sha256: options.sha256,
      kind: options.kind ?? "blob",
      description: options.description ?? null,
    });
  }

  /** Advanced: emit a raw event. Prefer the named helpers above. */
  async emit(
    eventType: string,
    payload?: Record<string, unknown>,
    options?: EmitOptions
  ): Promise<Record<string, unknown>> {
    return this._emit(eventType, payload ?? {}, options ?? {});
  }

  // ---- Lifecycle -----------------------------------------------------------

  async commit(): Promise<Record<string, unknown>> {
    if (this._closed) {
      throw new ValidationError("run already closed");
    }
    await this._emit("agent_run.committed", {});
    await this._flush();
    this._status = "committed";
    this._committed = true;
    this._closed = true;
    return this._client.transport.commitRun(this.runId);
  }

  async rollback(reason?: string): Promise<Record<string, unknown>> {
    if (this._closed) {
      throw new ValidationError("run already closed");
    }
    await this._emit("agent_run.rolled_back", { reason: reason ?? null });
    await this._flush();
    this._status = "rolled_back";
    this._closed = true;
    return this._client.transport.rollbackRun(this.runId, reason);
  }

  async fail(error?: Error): Promise<void> {
    if (this._closed) return;
    await this._emit("agent_run.failed", {
      error: error
        ? {
            type: error.name,
            message: error.message,
            stack: error.stack ?? null,
          }
        : null,
    });
    await this._flush();
    this._status = "failed";
    this._closed = true;
  }

  async flush(): Promise<void> {
    await this._flush();
  }

  get isClosed(): boolean {
    return this._closed;
  }

  get isCommitted(): boolean {
    return this._committed;
  }
}
