# @controldb/sdk

TypeScript / JavaScript SDK for ControlDB — tamper-evident audit trail for regulated AI agents.

## Install

```bash
npm install @controldb/sdk
```

Node.js 18+ is required (uses built-in `fetch` and `node:crypto`).

## Quick start

```typescript
import { ControlDB } from "@controldb/sdk";

const control = new ControlDB({
  apiKey: process.env.CONTROLDB_API_KEY,
  project: "aml",
  environment: "prod",
});

// --- withRun: auto-commit on success, auto-fail on error ---
const result = await control.withRun(
  { agentId: "aml-agent", agentVersion: "v1.2.0" },
  async (run) => {
    // Record a tool call
    await run.toolCall("customer-lookup", {
      input: { customerId: "cust_123" },
      output: { name: "Jane Doe", riskScore: 0.12 },
    });

    // Record a model call
    await run.modelCall({
      model: "gpt-4o",
      prompt: { system: "You are a compliance analyst." },
      completion: { text: "Low risk customer." },
      inputTokens: 120,
      outputTokens: 30,
    });

    // Record a state change
    await run.stateChange({
      entityType: "customer",
      entityId: "cust_123",
      before: { status: "pending" },
      after: { status: "approved" },
      reason: "Risk score below threshold",
    });

    // Policy check
    const policyResult = await run.policyCheck({
      policyId: "aml-v2",
      input: { customerId: "cust_123", action: "transfer" },
      result: { allowed: true },
    });

    return policyResult;
  }
);
```

## Manual run lifecycle

```typescript
const run = control.run({ agentId: "my-agent" });
try {
  await run.input({ userId: "user_abc" });
  await run.toolCall("search", { input: { q: "query" }, output: { hits: 5 } });
  await run.commit();
} catch (err) {
  await run.fail(err instanceof Error ? err : new Error(String(err)));
  throw err;
}
```

## Tool call context (for streaming / manual start/finish)

```typescript
const tool = run.startToolCall("process-document");
tool.input({ docId: "doc_999" });
// ... do work ...
tool.output({ summary: "...", pages: 12 });
await tool.finish();
```

## Payload modes

```typescript
import { ControlDB, PayloadMode } from "@controldb/sdk";

const control = new ControlDB({
  apiKey: "...",
  project: "my-project",
  defaultPayloadMode: PayloadMode.HASH_ONLY, // store only hashes, no raw data
});
```

## Redaction rules

```typescript
const control = new ControlDB({
  apiKey: "...",
  project: "my-project",
  redactionRules: [
    { field: "password", mode: "DROP" },
    { field: "ssn",      mode: "HASH" },
    { regex: "\\b\\d{16}\\b", mode: "REDACT" }, // credit card numbers
  ],
});
```

## LangChain integration

```typescript
import { buildHandler } from "@controldb/sdk/integrations/langchain";

await control.withRun({ agentId: "lc-agent" }, async (run) => {
  const handler = buildHandler(run);
  await chain.invoke({ question: "..." }, { callbacks: [handler] });
});
```

## OpenTelemetry trace propagation

```typescript
import { currentTraceIds } from "@controldb/sdk/integrations/opentelemetry";

const { traceId, spanId } = await currentTraceIds();
```

## Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `CONTROLDB_API_KEY` | API key | — (required) |
| `CONTROLDB_BASE_URL` | Collector URL | `http://localhost:8080` |
| `CONTROLDB_PROJECT` | Project ID | — (required) |
| `CONTROLDB_ORG_ID` | Organisation ID | `org_default` |
