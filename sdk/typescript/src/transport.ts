/**
 * HTTP transport with retry, batching, and an offline spool.
 *
 * Uses Node 18+ built-in fetch. No external HTTP dependencies.
 * Mirrors sdk/python/controldb/transport.py exactly.
 */

import { createWriteStream, existsSync, mkdirSync, readdirSync, readFileSync, unlinkSync } from "node:fs";
import { join } from "node:path";

import {
  AuthenticationError,
  AuthorizationError,
  CollectorUnavailableError,
  IdempotencyConflictError,
  PayloadTooLargeError,
  ValidationError,
} from "./errors.js";
import type { EventEnvelope } from "./events.js";

export interface TransportOptions {
  maxRetries?: number;
  /** Initial backoff delay in milliseconds (default: 200) */
  baseDelay?: number;
  /** Maximum backoff delay in milliseconds (default: 5000) */
  maxDelay?: number;
  timeout?: number;
  failOpen?: boolean;
  spoolPath?: string;
}

// ---- Spool ---------------------------------------------------------------

/**
 * Append-only on-disk spool for offline buffering.
 * Each call to write() appends one JSON line to a shared spool file.
 */
export class Spool {
  private readonly filePath: string;

  constructor(spoolPath: string) {
    mkdirSync(spoolPath, { recursive: true });
    this.filePath = join(spoolPath, "spool.ndjson");
  }

  write(entry: Record<string, unknown>): void {
    try {
      const ws = createWriteStream(this.filePath, { flags: "a", encoding: "utf8" });
      ws.write(JSON.stringify(entry) + "\n");
      ws.end();
    } catch {
      // Best-effort: ignore spool write errors
    }
  }

  drain(): Array<Record<string, unknown>> {
    if (!existsSync(this.filePath)) return [];
    let content: string;
    try {
      content = readFileSync(this.filePath, "utf8");
      unlinkSync(this.filePath);
    } catch {
      return [];
    }
    const entries: Array<Record<string, unknown>> = [];
    for (const line of content.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      try {
        entries.push(JSON.parse(trimmed) as Record<string, unknown>);
      } catch {
        // Skip malformed lines
      }
    }
    return entries;
  }
}

// ---- Transport -----------------------------------------------------------

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isRetryableStatus(status: number): boolean {
  return status >= 500;
}

async function raiseForStatus(resp: Response): Promise<void> {
  if (resp.status < 400) return;

  let message: string;
  try {
    const body = (await resp.json()) as Record<string, unknown>;
    message = (typeof body["detail"] === "string" ? body["detail"] : null) ?? resp.statusText;
  } catch {
    message = resp.statusText || String(resp.status);
  }

  switch (resp.status) {
    case 401:
      throw new AuthenticationError(message);
    case 403:
      throw new AuthorizationError(message);
    case 409:
      throw new IdempotencyConflictError(message);
    case 413:
      throw new PayloadTooLargeError(message);
    case 422:
      throw new ValidationError(message);
    default:
      throw new CollectorUnavailableError(message || `collector status ${resp.status}`);
  }
}

export class Transport {
  private readonly baseUrl: string;
  private readonly apiKey: string;
  private readonly maxRetries: number;
  private readonly baseDelay: number;
  private readonly maxDelay: number;
  private readonly failOpen: boolean;
  private readonly spool: Spool | null;

  constructor(baseUrl: string, apiKey: string, options: TransportOptions = {}) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.apiKey = apiKey;
    this.maxRetries = options.maxRetries ?? 4;
    this.baseDelay = options.baseDelay ?? 200;
    this.maxDelay = options.maxDelay ?? 5000;
    this.failOpen = options.failOpen ?? false;
    this.spool = options.spoolPath ? new Spool(options.spoolPath) : null;
  }

  async request(
    method: string,
    path: string,
    options: {
      body?: unknown;
      idempotencyKey?: string;
      retry?: boolean;
    } = {}
  ): Promise<Record<string, unknown>> {
    const { body, idempotencyKey, retry = true } = options;
    const attempts = retry ? this.maxRetries : 1;
    let lastError: unknown;

    for (let attempt = 0; attempt < attempts; attempt++) {
      try {
        const headers: Record<string, string> = {
          "Authorization": `Bearer ${this.apiKey}`,
          "Content-Type": "application/json",
          "User-Agent": "controldb-typescript/0.1",
        };
        if (idempotencyKey) {
          headers["Idempotency-Key"] = idempotencyKey;
        }

        const resp = await fetch(`${this.baseUrl}${path}`, {
          method,
          headers,
          body: body !== undefined ? JSON.stringify(body) : undefined,
        });

        // Retry on 5xx before throwing
        if (isRetryableStatus(resp.status) && attempt + 1 < attempts) {
          lastError = new CollectorUnavailableError(`collector status ${resp.status}`);
          const delay = Math.min(this.maxDelay, this.baseDelay * Math.pow(2, attempt));
          const jitter = Math.random() * (delay / 2);
          await sleep(delay + jitter);
          continue;
        }

        await raiseForStatus(resp);

        if (resp.status === 204 || resp.headers.get("content-length") === "0") {
          return {};
        }
        try {
          return (await resp.json()) as Record<string, unknown>;
        } catch {
          return {};
        }
      } catch (err) {
        // Non-retryable errors (auth, validation, etc.) propagate immediately
        if (
          err instanceof AuthenticationError ||
          err instanceof AuthorizationError ||
          err instanceof ValidationError ||
          err instanceof IdempotencyConflictError ||
          err instanceof PayloadTooLargeError
        ) {
          throw err;
        }
        lastError = err;
        if (attempt + 1 < attempts) {
          const delay = Math.min(this.maxDelay, this.baseDelay * Math.pow(2, attempt));
          const jitter = Math.random() * (delay / 2);
          await sleep(delay + jitter);
        }
      }
    }

    throw lastError instanceof Error
      ? lastError
      : new CollectorUnavailableError(String(lastError));
  }

  async postEvents(
    runId: string,
    events: EventEnvelope[],
    idempotencyKey?: string
  ): Promise<Record<string, unknown>> {
    try {
      return await this.request("POST", `/v1/runs/${runId}/events`, {
        body: { events },
        idempotencyKey,
      });
    } catch (err) {
      if (this.spool) {
        this.spool.write({ run_id: runId, events, idempotency_key: idempotencyKey ?? null });
        return { spooled: true, reason: String(err) };
      }
      if (this.failOpen) {
        return { spooled: false, fail_open: true, reason: String(err) };
      }
      throw err;
    }
  }

  async startRun(payload: Record<string, unknown>): Promise<Record<string, unknown>> {
    return this.request("POST", "/v1/runs/start", { body: payload });
  }

  async commitRun(runId: string): Promise<Record<string, unknown>> {
    return this.request("POST", `/v1/runs/${runId}/commit`);
  }

  async rollbackRun(runId: string, reason?: string): Promise<Record<string, unknown>> {
    return this.request("POST", `/v1/runs/${runId}/rollback`, {
      body: { reason: reason ?? null },
    });
  }

  async policyCheck(body: Record<string, unknown>): Promise<Record<string, unknown>> {
    return this.request("POST", "/v1/policy/check", { body });
  }

  async requestApproval(body: Record<string, unknown>): Promise<Record<string, unknown>> {
    return this.request("POST", "/v1/approvals/request", { body });
  }

  async getTimeline(runId: string): Promise<Record<string, unknown>> {
    return this.request("GET", `/v1/runs/${runId}/timeline`, { retry: false });
  }

  async verifyRun(runId: string): Promise<Record<string, unknown>> {
    return this.request("GET", `/v1/runs/${runId}/verify`, { retry: false });
  }

  async flushSpool(): Promise<number> {
    if (!this.spool) return 0;
    const drained = this.spool.drain();
    let flushed = 0;
    for (const entry of drained) {
      try {
        await this.request("POST", `/v1/runs/${entry["run_id"]}/events`, {
          body: { events: entry["events"] },
          idempotencyKey: (entry["idempotency_key"] as string | undefined) ?? undefined,
        });
        flushed++;
      } catch {
        // Re-queue and stop draining on first error
        this.spool.write(entry);
        break;
      }
    }
    return flushed;
  }

  async close(): Promise<void> {
    // No persistent connections to close with fetch; included for API parity
  }
}
