/**
 * OpenAI Agents SDK integration.
 *
 * Usage:
 *   import { ControlDB } from "@controldb/sdk";
 *   import { buildTracer } from "@controldb/sdk/integrations/openai-agents";
 *
 *   const control = new ControlDB({ ... });
 *   await control.withRun({ agentId: "my-agent" }, async (run) => {
 *     const tracer = buildTracer(run);
 *     // pass tracer to your OpenAI Agents pipeline
 *   });
 *
 * The integration emits model_call and tool_call events for each agent step.
 * @openai/agents is an optional dependency.
 */

import type { Run } from "../run.js";

export interface OpenAIAgentsTracer {
  onAgentStart(agentName: string, input: unknown): Promise<void>;
  onAgentEnd(agentName: string, output: unknown): Promise<void>;
  onAgentError(agentName: string, error: Error): Promise<void>;
  onToolStart(toolName: string, input: unknown): Promise<void>;
  onToolEnd(toolName: string, output: unknown): Promise<void>;
  onToolError(toolName: string, error: Error): Promise<void>;
  onModelStart(model: string, input: unknown): Promise<void>;
  onModelEnd(model: string, output: unknown): Promise<void>;
  onModelError(model: string, error: Error): Promise<void>;
}

/**
 * Build a tracer object compatible with the OpenAI Agents SDK tracing hooks.
 * All events are recorded into the given ControlDB Run.
 */
export function buildTracer(run: Run): OpenAIAgentsTracer {
  return {
    async onAgentStart(agentName: string, input: unknown): Promise<void> {
      try {
        await run.emit("agent_run.started", {
          agent_name: agentName,
          input: input as Record<string, unknown>,
        });
      } catch {
        // Guard: never break the agent pipeline
      }
    },

    async onAgentEnd(agentName: string, output: unknown): Promise<void> {
      try {
        await run.emit("agent_run.committed", {
          agent_name: agentName,
          output: output as Record<string, unknown>,
        });
      } catch {
        // Guard
      }
    },

    async onAgentError(agentName: string, error: Error): Promise<void> {
      try {
        await run.emit("agent_run.failed", {
          agent_name: agentName,
          error: { type: error.name, message: error.message },
        });
      } catch {
        // Guard
      }
    },

    async onToolStart(toolName: string, input: unknown): Promise<void> {
      try {
        await run.emit("tool_call.started", {
          name: toolName,
          input: input as Record<string, unknown>,
        });
      } catch {
        // Guard
      }
    },

    async onToolEnd(toolName: string, output: unknown): Promise<void> {
      try {
        await run.emit("tool_call.completed", {
          name: toolName,
          output: output as Record<string, unknown>,
        });
      } catch {
        // Guard
      }
    },

    async onToolError(toolName: string, error: Error): Promise<void> {
      try {
        await run.emit("tool_call.failed", {
          name: toolName,
          error: { type: error.name, message: error.message },
        });
      } catch {
        // Guard
      }
    },

    async onModelStart(model: string, input: unknown): Promise<void> {
      try {
        await run.emit("model_call.started", {
          model,
          input: input as Record<string, unknown>,
        });
      } catch {
        // Guard
      }
    },

    async onModelEnd(model: string, output: unknown): Promise<void> {
      try {
        await run.emit("model_call.completed", {
          model,
          output: output as Record<string, unknown>,
        });
      } catch {
        // Guard
      }
    },

    async onModelError(model: string, error: Error): Promise<void> {
      try {
        await run.emit("model_call.failed", {
          model,
          error: { type: error.name, message: error.message },
        });
      } catch {
        // Guard
      }
    },
  };
}
