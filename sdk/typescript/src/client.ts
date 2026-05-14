/**
 * ControlDB SDK client entrypoint.
 * Mirrors sdk/python/controldb/client.py exactly.
 *
 * Example:
 *   import { ControlDB } from "@controldb/sdk";
 *
 *   const control = new ControlDB({ apiKey: "...", project: "aml", environment: "prod" });
 *   await control.withRun({ agentId: "aml-agent", agentVersion: "v1" }, async (run) => {
 *     await run.toolCall("lookup", { input: { id: 1 }, output: { ok: true } });
 *   });
 */

import { ValidationError } from "./errors.js";
import { PayloadMode } from "./events.js";
import { generateRunId } from "./ids.js";
import type { RedactionRule } from "./redaction.js";
import { Run } from "./run.js";
import { Transport, TransportOptions } from "./transport.js";

export interface ControlDBOptions {
  /** API key. Falls back to CONTROLDB_API_KEY env var. */
  apiKey?: string;
  /** Collector base URL. Falls back to CONTROLDB_URL env var, then http://localhost:8080. */
  baseUrl?: string;
  /** Organisation identifier. Falls back to CONTROLDB_ORG_ID or "org_default". */
  orgId?: string;
  /** Project identifier. Falls back to CONTROLDB_PROJECT env var. */
  project?: string;
  /** Deployment environment label (default: "dev"). */
  environment?: string;
  /** Default payload capture mode (default: PayloadMode.REDACTED_PAYLOAD). */
  defaultPayloadMode?: PayloadMode | string;
  /** Redaction rules applied to all payloads before capture. */
  redactionRules?: RedactionRule[];
  /** Number of events to buffer before auto-flushing (default: 16). */
  batchSize?: number;
  /** If true, swallow transport errors instead of throwing. */
  failOpen?: boolean;
  /** Directory path for offline spool files. */
  spoolPath?: string;
  /** Inject a custom Transport (useful for testing). */
  transport?: Transport;
}

export class ControlDB {
  readonly orgId: string;
  readonly project: string;
  readonly environment: string;
  readonly defaultPayloadMode: string;
  readonly redactionRules: RedactionRule[];
  readonly batchSize: number;
  readonly transport: Transport;

  private readonly _openRuns: Run[] = [];

  constructor(options: ControlDBOptions = {}) {
    const apiKey = options.apiKey ?? process.env["CONTROLDB_API_KEY"];
    if (!apiKey) {
      throw new ValidationError(
        "apiKey is required (or set CONTROLDB_API_KEY environment variable)"
      );
    }

    const project =
      options.project ?? process.env["CONTROLDB_PROJECT"];
    if (!project) {
      throw new ValidationError(
        "project is required (or set CONTROLDB_PROJECT environment variable)"
      );
    }

    this.orgId =
      options.orgId ?? process.env["CONTROLDB_ORG_ID"] ?? "org_default";
    this.project = project;
    this.environment = options.environment ?? "dev";
    this.defaultPayloadMode =
      options.defaultPayloadMode ?? PayloadMode.REDACTED_PAYLOAD;
    this.redactionRules = options.redactionRules ?? [];
    this.batchSize = options.batchSize ?? 16;

    const baseUrl =
      options.baseUrl ??
      process.env["CONTROLDB_BASE_URL"] ??
      process.env["CONTROLDB_URL"] ??
      "http://localhost:8080";

    const transportOpts: TransportOptions = {
      failOpen: options.failOpen ?? false,
      spoolPath: options.spoolPath,
    };

    this.transport = options.transport ?? new Transport(baseUrl, apiKey, transportOpts);

    // Flush on process exit (best-effort)
    process.on("exit", () => {
      void this._atExitFlush();
    });
  }

  /**
   * Create a Run, register it with the collector, and return it.
   * The caller must call run.commit() / run.rollback() / run.fail() manually.
   */
  run(options: {
    agentId: string;
    agentVersion?: string;
    environment?: string;
    traceId?: string;
    runId?: string;
    metadata?: Record<string, unknown>;
  }): Run {
    const {
      agentId,
      agentVersion,
      environment,
      traceId,
      runId: explicitRunId,
      metadata,
    } = options;

    const assignedRunId = explicitRunId ?? generateRunId();

    // Fire-and-forget the start_run API call (Python SDK does it synchronously;
    // we do it async but don't await here to match the synchronous Python API shape).
    void this.transport
      .startRun({
        agent_id: agentId,
        agent_version: agentVersion ?? null,
        environment: environment ?? this.environment,
        org_id: this.orgId,
        project_id: this.project,
        metadata: metadata ?? {},
        run_id: assignedRunId,
      })
      .catch(() => {
        /* fail-open: the run continues even if startRun fails */
      });

    const r = new Run(
      this,
      assignedRunId,
      agentId,
      agentVersion,
      environment ?? this.environment,
      traceId
    );
    this._openRuns.push(r);
    return r;
  }

  /**
   * Create a Run, execute fn(run), auto-commit on success and auto-fail on error.
   * This mirrors Python's `with control.run(...) as run:` idiom.
   */
  async withRun<T>(
    options: {
      agentId: string;
      agentVersion?: string;
      environment?: string;
      traceId?: string;
      runId?: string;
      metadata?: Record<string, unknown>;
    },
    fn: (run: Run) => Promise<T>
  ): Promise<T> {
    const r = this.run(options);
    try {
      const result = await fn(r);
      if (!r.isClosed) {
        await r.commit();
      }
      return result;
    } catch (err) {
      if (!r.isClosed) {
        await r.fail(err instanceof Error ? err : new Error(String(err)));
      }
      throw err;
    } finally {
      this._removeOpenRun(r);
    }
  }

  // ---- Dashboard / API helpers --------------------------------------------

  async timeline(runId: string): Promise<Record<string, unknown>> {
    return this.transport.getTimeline(runId);
  }

  async verify(runId: string): Promise<Record<string, unknown>> {
    return this.transport.verifyRun(runId);
  }

  async flushSpool(): Promise<number> {
    return this.transport.flushSpool();
  }

  async close(): Promise<void> {
    for (const r of [...this._openRuns]) {
      if (!r.isClosed) {
        try {
          await r.flush();
        } catch {
          // Best-effort
        }
      }
    }
    await this.transport.close();
  }

  private _removeOpenRun(r: Run): void {
    const idx = this._openRuns.indexOf(r);
    if (idx !== -1) this._openRuns.splice(idx, 1);
  }

  private async _atExitFlush(): Promise<void> {
    for (const r of this._openRuns) {
      if (!r.isClosed) {
        try {
          await r.flush();
        } catch {
          // Best-effort
        }
      }
    }
    try {
      await this.transport.close();
    } catch {
      // Best-effort
    }
  }
}

export default ControlDB;
