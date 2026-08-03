export type Competition = "male" | "female" | "futsal";
export type BatchStatus = "incomplete" | "ready" | "drafted";

export interface User {
  id: number;
  username: string;
  display_name: string;
  role: "admin" | "writer";
  is_active: boolean;
}

export interface ApiErrorDetails {
  [key: string]: unknown;
}

export interface BatchError {
  code: string;
  message: string | null;
  at: string | null;
}

export interface Cover {
  kind: "file" | "media_id";
  storage_key: string;
  content_type: string | null;
}

export interface Weather {
  date: string;
  adcode: string;
  region_name: string;
  condition: string;
  low_c: number;
  high_c: number;
  wind_direction: string;
  wind_level: string;
  source: "auto" | "manual";
  report_time: string;
}

export interface TeamRefSnapshot {
  team_id: number;
  name: string;
  short_name: string;
}

export interface SeasonOutcomeSnapshot {
  season: string;
  competition_label: string | null;
  outcome: string;
}

export interface PlayedMatchSnapshot {
  game_id: number;
  home: TeamRefSnapshot;
  away: TeamRefSnapshot;
  home_score?: number | null;
  away_score?: number | null;
  home_penalty?: number | null;
  away_penalty?: number | null;
  result_text: string;
  season?: string | null;
  competition_label?: string | null;
  stage?: string | null;
}

export interface TeamSnapshot extends TeamRefSnapshot {
  previous_outcomes: SeasonOutcomeSnapshot[];
  current_results: PlayedMatchSnapshot[];
}

export interface PreviewMatch {
  game_id: number;
  batch_id: number;
  tournament_id: number;
  tournament_name: string;
  competition_name: string;
  stage: string;
  kickoff: string;
  venue: string;
  home: TeamSnapshot;
  away: TeamSnapshot;
  head_to_head: PlayedMatchSnapshot[];
  active: boolean;
  task_open: boolean;
  claimed_by_user_id: number | null;
  writers: string[];
  body: string;
  body_version: number;
  updated_at: string;
}

export interface PreviewBatch {
  id: number;
  preview_date: string;
  competition: Competition;
  status: BatchStatus;
  headline: string;
  editors: string[];
  reviewers: string[];
  approvers: string[];
  cover: Cover;
  current_article_id: number | null;
  latest_article_id: number | null;
  missing_fields: string[];
  last_error: BatchError | null;
  created_at: string;
  updated_at: string;
  weather?: Weather | null;
  matches?: PreviewMatch[];
}

export interface Article {
  id: number;
  batch_id: number;
  version_number: number;
  title: string;
  body_html: string;
  author: string;
  digest: string;
  source_url: string;
  template_version: string;
  content_fingerprint: string;
  cover_kind: "file" | "media_id";
  cover_storage_key: string;
  cover_sha256: string;
  is_complete: boolean;
  missing_fields: string[];
  input_snapshot: Record<string, unknown>;
  created_at: string;
  is_current: boolean | null;
}

export interface CreateBatchResult {
  date: string;
  competition: Competition;
  status: "created" | "reused" | "skipped" | "failed";
  batch_id?: number;
  warning?: string;
  reason?: string;
  error?: { code: string; message: string };
}

export interface EditorialDefaults {
  editors: string[];
  reviewers: string[];
  approvers: string[];
  updated_at: string;
}

export interface CredentialStatus {
  configured: boolean;
  openid_masked: string | null;
  session_key_masked: string | null;
  user_registered?: boolean;
  updated_at?: string;
}

export interface DraftArticleComponent {
  article_id: number;
  content_fingerprint: string;
  cover_sha256: string;
}

export interface WechatDraft {
  id: number;
  articles: DraftArticleComponent[];
  publication_fingerprint: string;
  media_id: string;
  wechat_created_at: string;
  created_at: string;
}

export type DraftResponse =
  | {
      status: "ready";
      publication_fingerprint: string;
      articles: DraftArticleComponent[];
    }
  | { status: "created" | "reused"; draft: WechatDraft };

export const competitionLabels: Record<Competition, string> = {
  male: "男足",
  female: "女足",
  futsal: "五人制",
};

export const statusLabels: Record<BatchStatus, string> = {
  incomplete: "待完善",
  ready: "可发布",
  drafted: "已建草稿",
};

export const missingFieldLabels: Record<string, string> = {
  headline: "标题",
  weather: "天气",
  editors: "编辑",
  reviewers: "责编",
  approvers: "审核",
  matches: "比赛",
  writers: "作者",
  body: "正文",
};

interface MissingFieldMatch {
  game_id: number;
  home: TeamRefSnapshot;
  away: TeamRefSnapshot;
}

function missingFieldTeamName(value: TeamRefSnapshot): string {
  return value.short_name || value.name;
}

export function labelMissingField(
  value: string,
  matches: readonly MissingFieldMatch[] = [],
): string {
  const matchField = /^matches\.(\d+)\.(writers|body)$/.exec(value);
  if (matchField) {
    const gameId = Number(matchField[1]);
    const match = matches.find((item) => item.game_id === gameId);
    const matchName = match
      ? `${missingFieldTeamName(match.home)} vs ${missingFieldTeamName(match.away)}`
      : `比赛 #${gameId}`;
    return `${matchName} · ${missingFieldLabels[matchField[2]]}`;
  }
  return missingFieldLabels[value] ?? value;
}
