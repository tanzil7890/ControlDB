/**
 * LangChain callback handler integration.
 *
 * Usage:
 *   import { ControlDB } from "@controldb/sdk";
 *   import { buildHandler } from "@controldb/sdk/integrations/langchain";
 *
 *   const control = new ControlDB({ ... });
 *   await control.withRun({ agentId: "my-agent" }, async (run) => {
 *     const handler = buildHandler(run);
 *     await chain.invoke({...}, { callbacks: [handler] });
 *   });
 *
 * Requires @langchain/core to be installed.
 * Mirrors sdk/python/controldb/integrations/langchain.py exactly.
 */

import type { Run } from "../run.js";

export interface LangChainCallbackHandler {
  handleLLMStart(serialized: Record<string, unknown>, prompts: string[], kwargs?: Record<string, unknown>): Promise<void>;
  handleLLMEnd(response: unknown, kwargs?: Record<string, unknown>): Promise<void>;
  handleLLMError(error: Error, kwargs?: Record<string, unknown>): Promise<void>;
  handleToolStart(serialized: Record<string, unknown>, inputStr: string, kwargs?: Record<string, unknown>): Promise<void>;
  handleToolEnd(output: string, kwargs?: Record<string, unknown>): Promise<void>;
  handleToolError(error: Error, kwargs?: Record<string, unknown>): Promise<void>;
}

/**
 * Build a LangChain-compatible callback handler that records model and tool
 * events into the given ControlDB Run.
 *
 * The handler is a plain object implementing the LangChain BaseCallbackHandler
 * interface so it works without requiring @langchain/core at import time.
 */
export function buildHandler(run: Run): LangChainCallbackHandler {
  return {
    async handleLLMStart(
      serialized: Record<string, unknown>,
      prompts: string[]
    ): Promise<void> {
      try {
        await run.emit("model_call.started", {
          model: (serialized["name"] as string) ?? null,
          prompts,
        });
      } catch {
        // Guard: never break the LangChain pipeline
      }
    },

    async handleLLMEnd(response: unknown): Promise<void> {
      try {
        const generations =
          response !== null &&
          typeof response === "object" &&
          "generations" in (response as object)
            ? (response as Record<string, unknown>)["generations"]
            : String(response);
        await run.emit("model_call.completed", { response: generations });
      } catch {
        // Guard
      }
    },

    async handleLLMError(error: Error): Promise<void> {
      try {
        await run.emit("model_call.failed", {
          error: { type: error.name, message: error.message },
        });
      } catch {
        // Guard
      }
    },

    async handleToolStart(
      serialized: Record<string, unknown>,
      inputStr: string
    ): Promise<void> {
      try {
        await run.emit("tool_call.started", {
          name: (serialized["name"] as string) ?? null,
          input: inputStr,
        });
      } catch {
        // Guard
      }
    },

    async handleToolEnd(output: string): Promise<void> {
      try {
        await run.emit("tool_call.completed", { output });
      } catch {
        // Guard
      }
    },

    async handleToolError(error: Error): Promise<void> {
      try {
        await run.emit("tool_call.failed", {
          error: { type: error.name, message: error.message },
        });
      } catch {
        // Guard
      }
    },
  };
}
