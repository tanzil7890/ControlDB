/**
 * OpenTelemetry trace propagation helpers.
 *
 * If @opentelemetry/api is installed, currentTraceIds() reads the active span
 * and returns the W3C trace context IDs the SDK should attach to events.
 * When OTel is not installed, returns an empty object.
 *
 * Mirrors sdk/python/controldb/integrations/opentelemetry.py exactly.
 */

export interface TraceIds {
  traceId?: string;
  spanId?: string;
  parentSpanId?: string;
}

/**
 * Returns trace/span IDs from the active OpenTelemetry span, if available.
 * Falls back gracefully to an empty object when @opentelemetry/api is not installed.
 */
export async function currentTraceIds(): Promise<TraceIds> {
  try {
    // Dynamic import so @opentelemetry/api remains optional
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const otelApi = await import("@opentelemetry/api");
    const span = otelApi.trace.getActiveSpan();
    if (!span) return {};
    const context = span.spanContext();
    if (!context || !otelApi.isSpanContextValid(context)) return {};

    const traceId = context.traceId; // already 32-char hex string
    const spanId = context.spanId;   // already 16-char hex string
    return { traceId, spanId };
  } catch {
    // OTel not installed or no active span
    return {};
  }
}

/**
 * Synchronous variant — returns an empty object if OTel is not available.
 * Use this when you cannot await.
 */
export function currentTraceIdsSync(): TraceIds {
  try {
    // Synchronous require (CommonJS only; works in Node.js)
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const otelApi = require("@opentelemetry/api") as typeof import("@opentelemetry/api");
    const span = otelApi.trace.getActiveSpan();
    if (!span) return {};
    const context = span.spanContext();
    if (!context || !otelApi.isSpanContextValid(context)) return {};
    return { traceId: context.traceId, spanId: context.spanId };
  } catch {
    return {};
  }
}
