/**
 * Minimal ambient declarations for the OpenClaw plugin SDK.
 *
 * OpenClaw itself is NOT a dependency of this package: its engine requirement
 * (Node 22.22.3+/24.15+/25.9+) is stricter than what we build/test against, and
 * the host provides these modules at load time. We declare only the surface this
 * adapter actually touches, loosely typed (`unknown`/optional) so we never claim
 * a shape we haven't verified against a live host. All real logic lives in
 * bridge/translate/notes, which import none of this.
 *
 * Verify against the running SDK before release; see openclaw/README.md.
 */
declare module "openclaw/plugin-sdk/plugin-entry" {
  export interface RequireApproval {
    title: string;
    description: string;
    severity?: "info" | "warning" | "critical";
    timeoutMs?: number;
  }

  export interface ToolCallResult {
    params?: Record<string, unknown>;
    block?: boolean;
    blockReason?: string;
    requireApproval?: RequireApproval;
  }

  export interface PromptBuildResult {
    prependContext?: string;
    appendContext?: string;
  }

  export interface NextTurnInjection {
    idempotencyKey?: string;
    prependContext?: string;
    appendContext?: string;
    expiresAtMs?: number;
  }

  export interface OpenClawPluginApi {
    id: string;
    name: string;
    logger?: {
      debug?: (msg: string, ...rest: unknown[]) => void;
      warn?: (msg: string, ...rest: unknown[]) => void;
      error?: (msg: string, ...rest: unknown[]) => void;
    };
    session?: {
      workflow?: {
        enqueueNextTurnInjection?: (injection: NextTurnInjection) => unknown;
      };
    };
    on(event: string, handler: (event: any, ctx: any) => unknown): void;
  }

  export interface PluginEntryConfig {
    id: string;
    name: string;
    description?: string;
    register(api: OpenClawPluginApi): void | Promise<void>;
  }

  export function definePluginEntry(config: PluginEntryConfig): unknown;
}
