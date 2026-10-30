// Thin typed client over the real backend. No mocks: every call hits VITE_API_BASE_URL.

/**
 * Normalize the configured backend base URL. A full URL passes through (minus a
 * trailing slash). A bare host — what a Render Blueprint `fromService ... property: host`
 * reference yields — gets `https://`, and a dot-less Render service host gets
 * `.onrender.com` appended. localhost stays on plain http.
 */
export function resolveApiBase(raw: string | undefined): string {
  const value = (raw ?? "").trim().replace(/\/+$/, "");
  if (!value) return "http://localhost:8000";
  if (/^https?:\/\//i.test(value)) return value;
  if (/^(localhost|127\.0\.0\.1)(:\d+)?$/.test(value)) return `http://${value}`;
  const host = value.includes(".") ? value : `${value}.onrender.com`;
  return `https://${host}`;
}

export const API_BASE: string = resolveApiBase(import.meta.env.VITE_API_BASE_URL);

const TOKEN_KEY = "chessmind.tokens";

export interface Tokens {
  access_token: string;
  refresh_token: string;
}

export function loadTokens(): Tokens | null {
  try {
    const raw = localStorage.getItem(TOKEN_KEY);
    return raw ? (JSON.parse(raw) as Tokens) : null;
  } catch {
    return null;
  }
}

export function saveTokens(t: Tokens | null): void {
  try {
    if (t) localStorage.setItem(TOKEN_KEY, JSON.stringify(t));
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: session-only auth */
  }
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

let refreshing: Promise<boolean> | null = null;

async function refreshTokens(): Promise<boolean> {
  const t = loadTokens();
  if (!t) return false;
  refreshing ??= (async () => {
    try {
      const r = await fetch(`${API_BASE}/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "ngrok-skip-browser-warning": "true" },
        body: JSON.stringify({ refresh_token: t.refresh_token }),
      });
      if (!r.ok) return false;
      saveTokens((await r.json()) as Tokens);
      return true;
    } catch {
      return false;
    } finally {
      refreshing = null;
    }
  })();
  return refreshing;
}

async function request<T>(method: string, path: string, body?: unknown, retry = true): Promise<T> {
  const headers: Record<string, string> = { "ngrok-skip-browser-warning": "true" };
  const tokens = loadTokens();
  if (tokens) headers.Authorization = `Bearer ${tokens.access_token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 401 && retry && (await refreshTokens())) return request<T>(method, path, body, false);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

// ---- types (mirror backend/chessmind/schemas) ----

export interface Player {
  id: string;
  email: string;
  display_name: string;
  preferred_color: "white" | "black" | "either";
  settings_json: Record<string, unknown>;
}

export type SessionMode = "guided" | "blindfold" | "opponent";
export type SamplerMode = "mainline" | "mix" | "surprise" | "opponent_twin";

export interface SessionState {
  id: string;
  mode: string;
  opponent_profile_id: string | null;
  seed: number;
  settings_json: Record<string, unknown>;
  state_json: {
    current_fen: string;
    move_history_san: string[];
    player_color: "white" | "black";
    next_to_move: "player" | "opponent";
    status: "active" | "finished";
    last_opponent_move: OpponentMove | null;
    reveals_used: number;
    reveals_budget: number;
    hints_used: number;
  };
}

export interface OpponentMove {
  san: string;
  uci: string;
  source_tier: string;
  confidence: number;
  sample_size: number;
}

export interface LegalMoves {
  session_id: string;
  current_fen: string;
  next_to_move: "player" | "opponent";
  status: "active" | "finished";
  legal_moves_san: string[];
  legal_moves_uci: string[];
  reveals_used: number;
  reveals_budget: number;
  hints_used: number;
}

export interface MoveResult {
  played_san: string;
  played_uci: string;
  classification: string;
  eval_before_cp_white: number | null;
  eval_after_cp_white: number | null;
  eval_swing_cp: number;
  engine_version: string;
  engine_depth: number;
  best_lines: { line_rank: number; cp: number | null; mate: number | null; pv_san: string[] }[];
  coaching: { spoken_summary: string; correction_san: string | null; evidence_refs: string[] };
  opponent_move: OpponentMove | null;
  current_fen: string;
  status: "active" | "finished";
}

export type QuestionKind = "piece_location" | "square_content" | "hanging_pieces" | "side_to_move" | "evaluation";

export interface AskResult {
  understood: boolean;
  question_id: string | null;
  kind: QuestionKind | null;
  answer_text: string;
  facts: Record<string, unknown>;
  questions_asked: number;
}

export interface QuizQuestion {
  question_id: string;
  kind: QuestionKind;
  prompt: string;
}

export interface QuestionTally {
  asked: number;
  quizzed: number;
  answered: number;
  correct: number;
  mean_score: number | null;
}

export interface QuizAnswer {
  question_id: string;
  understood: boolean;
  correct: boolean;
  score: number | null;
  expected_text: string | null;
  feedback: string;
  tally: QuestionTally;
}

export interface RevealResult {
  reveals_used: number;
  reveals_budget: number;
  hints_used: number;
  current_fen: string;
  was_free: boolean;
}

export interface OpeningEdge {
  uci: string;
  san: string;
  to_position_id: string;
  games_count: number;
  white_wins: number;
  black_wins: number;
  draws: number;
}

export interface MasterMove {
  uci: string;
  san: string;
  games: number;
  white: number;
  draws: number;
  black: number;
  average_rating: number | null;
}

/** Real master-game stats from the Lichess explorer; kept apart from local (imported/seed) counts. */
export interface MastersStats {
  source: string;
  attribution: string;
  explorer_url: string;
  status: "live" | "cached" | "stale" | "unavailable" | "disabled";
  fetched_at: string | null;
  error: string | null;
  total_games: number;
  white: number;
  draws: number;
  black: number;
  opening_eco: string | null;
  opening_name: string | null;
  moves: MasterMove[];
}

export interface OpeningTree {
  root: {
    position_id: string;
    canonical_key: string;
    fen: string;
    eco: string | null;
    opening_name: string | null;
    popularity: number;
    edges: OpeningEdge[];
  };
  total_positions: number;
  total_edges: number;
  local_stats_note: string;
  masters: MastersStats | null;
}

export interface MasteryRow {
  position_id: string;
  canonical_key: string;
  fen: string;
  opening_name: string | null;
  state: string;
  recall_score: number;
  understanding_score: number;
  visualization_score: number;
  speed_score: number;
  due_at: string;
  last_tested_at: string | null;
  ease_factor: number;
  interval_days: number;
  reps: number;
}

export interface NextDrill {
  due_now: boolean;
  row: MasteryRow | null;
  reason: string;
}

export interface MasteryDashboard {
  counts_by_state: Record<string, number>;
  due_now: number;
  due_next_24h: number;
  weakest: MasteryRow[];
  due_soon: MasteryRow[];
}

export interface Opponent {
  id: string;
  display_name: string;
  games_imported: number;
  last_import_at: string | null;
}

export interface OpponentTree {
  position_fen: string;
  opponent_color: string;
  sample_total: number;
  edges: {
    move_uci: string;
    move_san: string;
    games_count: number;
    wins: number;
    losses: number;
    draws: number;
    confidence: number;
    sample_size_bucket: string;
  }[];
}

export type ImportIntent = "repertoire" | "opponent" | "corpus";

export interface ImportJob {
  id: string;
  kind: string;
  status: string;
  progress_games: number;
  total_games: number;
  error: string | null;
  artifacts_json: {
    games?: number;
    positions_touched?: number;
    edges?: number;
    repertoire_lines?: number;
    errors?: string[];
  };
}

// ---- endpoints ----

export const api = {
  register: (email: string, password: string, display_name: string) =>
    request<Player>("POST", "/auth/register", { email, password, display_name }),
  login: (email: string, password: string) => request<Tokens>("POST", "/auth/login", { email, password }),
  me: () => request<Player>("GET", "/players/me"),
  patchSettings: (settings_json: Record<string, unknown>) =>
    request<Player>("PATCH", "/players/me", { settings_json }),

  createSession: (body: {
    mode: SessionMode;
    player_color: "white" | "black";
    sampler_mode: SamplerMode;
    opponent_profile_id?: string | null;
    starting_fen?: string;
    novelty_prob?: number;
    seed?: number;
  }) => request<SessionState>("POST", "/sessions", body),
  getSession: (id: string) => request<SessionState>("GET", `/sessions/${id}`),
  legal: (id: string) => request<LegalMoves>("GET", `/sessions/${id}/legal`),
  move: (id: string, body: { san?: string; uci?: string; latency_ms?: number }) =>
    request<MoveResult>("POST", `/sessions/${id}/move`, body),
  reveal: (id: string, reason?: string) => request<RevealResult>("POST", `/sessions/${id}/reveal`, { reason }),
  finish: (id: string) => request<unknown>("POST", `/sessions/${id}/finish`),
  ask: (id: string, alts: string[]) =>
    request<AskResult>("POST", `/sessions/${id}/ask`, { text: alts[0], alternatives: alts.slice(1) }),
  quiz: (id: string, kind?: QuestionKind) => request<QuizQuestion>("POST", `/sessions/${id}/quiz`, { kind }),
  answerQuiz: (id: string, questionId: string, alts: string[]) =>
    request<QuizAnswer>("POST", `/sessions/${id}/quiz/${questionId}/answer`, { text: alts[0], alternatives: alts.slice(1) }),
  masters: (fen: string) => request<MastersStats>("GET", `/openings/masters?fen=${encodeURIComponent(fen)}`),

  openingTree: (fen?: string) =>
    request<OpeningTree>("GET", `/openings/tree?limit=20${fen ? `&fen=${encodeURIComponent(fen)}` : ""}`),
  mastery: (playerId: string) => request<MasteryDashboard>("GET", `/players/${playerId}/mastery`),
  masteryRow: (playerId: string, positionId: string) =>
    request<MasteryRow>("GET", `/players/${playerId}/mastery/${positionId}`),
  nextDrill: (playerId: string) => request<NextDrill>("POST", `/players/${playerId}/mastery/next`),

  importPgn: (pgn: string, intent: ImportIntent, opponent_profile_id?: string, source = "upload") =>
    request<ImportJob>("POST", "/imports/pgn", { pgn, intent, opponent_profile_id, source }),

  opponents: () => request<Opponent[]>("GET", "/opponents"),
  createOpponent: (display_name: string) => request<Opponent>("POST", "/opponents", { display_name }),
  importJob: (id: string) => request<ImportJob>("GET", `/imports/${id}`),
  opponentTree: (id: string, fen?: string) =>
    request<OpponentTree>("GET", `/opponents/${id}/tree${fen ? `?fen=${encodeURIComponent(fen)}` : ""}`),
};
