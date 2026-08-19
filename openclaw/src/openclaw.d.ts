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

  // Verified against the real SDK type PluginNextTurnInjection (openclaw
  // 2026.7.x, via tsc against the installed package): sessionKey + text are
  // required, the rest optional. The content field is `text`, NOT appendContext
  // (appendContext is the before_prompt_build RESULT — see PromptBuildResult).
  export interface NextTurnInjection {
    sessionKey: string;
    text: string;
    placement?: string;
    idempotencyKey?: string;
    ttlMs?: number;
    metadata?: Record<string, unknown>;
  }

  // Verified against the real SDK (openclaw 2026.7.x): a command handler returns
  // a ReplyPayload (whose text field is `text`) plus continueAgent/suppressReply.
  // Omitting continueAgent delivers `text` and STOPS the turn — the read-only
  // path. continueAgent:true continues the turn to the LLM agent.
  export interface PluginCommandResult {
    text?: string;
    continueAgent?: boolean;
    suppressReply?: boolean;
  }

  export interface PluginCommandContext {
    /** Raw argument string after the command name (present iff acceptsArgs). */
    args?: string;
    commandBody?: string;
    sessionKey?: string;
    sessionId?: string;
    channel?: string;
    isAuthorizedSender?: boolean;
    [key: string]: unknown;
  }

  export interface PluginCommandDefinition {
    /** Invocation name without the leading slash (global namespace, no prefix). */
    name: string;
    description: string;
    acceptsArgs?: boolean;
    requireAuth?: boolean;
    nativeNames?: Record<string, string> & { default?: string };
    handler: (
      ctx: PluginCommandContext,
    ) => PluginCommandResult | Promise<PluginCommandResult>;
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
    // Optional: absent on hosts older than the command API — index.ts guards it.
    registerCommand?: (command: PluginCommandDefinition) => void;
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
