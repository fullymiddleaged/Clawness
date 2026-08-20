/**
 * memory.ts — host-agnostic logic for exposing `.clawness/memory.md` as a native
 * OpenClaw searchable memory corpus (via `registerMemoryCorpusSupplement`).
 *
 * ADDITIVE and non-displacing: Claude Code (and OpenClaw's own `before_prompt_build`
 * path) still inject the ranked memory block every turn. This only lets the model
 * pull a relevant lesson on demand through OpenClaw's native memory search. Ranking
 * is done by `openclaw/pyhooks/memory_corpus.py` (reusing `clawness.memory`), so the
 * shared engine is untouched. Parse/translation code lives here for unit-testing;
 * index.ts owns the OpenClaw contact. Fails toward empty results.
 *
 * Cwd caveat: the corpus `search`/`get` params carry NO cwd, so the supplement is
 * global. We resolve the project via a `getCwd()` the adapter keeps current from the
 * most recent session/prompt event, falling back to process.cwd(). Documented as a
 * known limit pending a live multi-workspace pass (see openclaw/EXTENSIONS-PLAN.md).
 */
import { runPythonHook } from "./bridge.js";

const MEMORY_CORPUS_HOOK = "openclaw/pyhooks/memory_corpus.py";

/** OpenClaw's `MemoryCorpusSearchResult` (the fields we populate). */
export interface MemorySearchResult {
  corpus: string;
  path: string;
  title?: string;
  score: number;
  snippet: string;
  id?: string;
  sourceType?: string;
  sourcePath?: string;
}

/** OpenClaw's `MemoryCorpusGetResult`. */
export interface MemoryGetResult {
  corpus: string;
  path: string;
  title?: string;
  content: string;
  fromLine: number;
  lineCount: number;
  id?: string;
}

/** The `MemoryCorpusSupplement` interface OpenClaw registers. */
export interface MemoryCorpusSupplement {
  search(params: { query: string; maxResults?: number; agentSessionKey?: string }): Promise<MemorySearchResult[]>;
  get(params: { lookup: string; agentSessionKey?: string }): Promise<MemoryGetResult | null>;
}

/** Parse memory_corpus.py search stdout into validated results (drops bad rows). */
export function parseSearchResults(stdout: string): MemorySearchResult[] {
  const text = (stdout ?? "").trim();
  if (!text) return [];
  let arr: unknown;
  try {
    arr = JSON.parse(text);
  } catch {
    return [];
  }
  if (!Array.isArray(arr)) return [];
  const out: MemorySearchResult[] = [];
  for (const row of arr) {
    const r = row as Record<string, unknown>;
    if (typeof r?.snippet !== "string") continue;
    out.push({
      corpus: typeof r.corpus === "string" ? r.corpus : "clawness-memory",
      path: typeof r.path === "string" ? r.path : ".clawness/memory.md",
      title: typeof r.title === "string" ? r.title : undefined,
      score: Number.isFinite(r.score as number) ? (r.score as number) : 0,
      snippet: r.snippet,
      id: typeof r.id === "string" ? r.id : undefined,
      sourceType: typeof r.sourceType === "string" ? r.sourceType : "clawness",
      sourcePath: typeof r.sourcePath === "string" ? r.sourcePath : undefined,
    });
  }
  return out;
}

/** Parse memory_corpus.py get stdout into a validated result, or null. */
export function parseGetResult(stdout: string): MemoryGetResult | null {
  const text = (stdout ?? "").trim();
  if (!text || text === "null") return null;
  let obj: unknown;
  try {
    obj = JSON.parse(text);
  } catch {
    return null;
  }
  const r = obj as Record<string, unknown>;
  if (!r || typeof r.content !== "string") return null;
  return {
    corpus: typeof r.corpus === "string" ? r.corpus : "clawness-memory",
    path: typeof r.path === "string" ? r.path : ".clawness/memory.md",
    title: typeof r.title === "string" ? r.title : undefined,
    content: r.content,
    fromLine: Number.isFinite(r.fromLine as number) ? (r.fromLine as number) : 1,
    lineCount: Number.isFinite(r.lineCount as number) ? (r.lineCount as number) : 1,
    id: typeof r.id === "string" ? r.id : undefined,
  };
}

/**
 * Build the corpus supplement. `getCwd` returns the current project root; the
 * supplement passes it to the Python ranker on each call. All failures degrade to
 * empty results so memory search never throws into the host.
 */
export function makeMemoryCorpusSupplement(getCwd: () => string): MemoryCorpusSupplement {
  return {
    async search({ query, maxResults }) {
      try {
        if (!query || !query.trim()) return [];
        const result = await runPythonHook(MEMORY_CORPUS_HOOK, {
          mode: "search",
          cwd: getCwd(),
          query,
          maxResults: maxResults ?? 8,
        });
        if (result.noPython) return [];
        return parseSearchResults(result.stdout);
      } catch {
        return [];
      }
    },
    async get({ lookup }) {
      try {
        if (!lookup || !lookup.trim()) return null;
        const result = await runPythonHook(MEMORY_CORPUS_HOOK, {
          mode: "get",
          cwd: getCwd(),
          lookup,
        });
        if (result.noPython) return null;
        return parseGetResult(result.stdout);
      } catch {
        return null;
      }
    },
  };
}
