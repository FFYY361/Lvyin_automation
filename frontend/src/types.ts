export type Competition = "male" | "female" | "futsal";
export type BatchStatus = "incomplete" | "ready" | "drafted";

export interface User {
  id: number;
  username: string;
  display_name: string;
  role: "admin" | "user";
  is_active: boolean;
}

export interface UserSummary {
  id: number;
  display_name: string;
}

export interface AdminUser extends User {
  created_at: string;
  updated_at: string;
  claimed_task_count: number;
}

export interface ApiErrorDetails {
  [key: string]: unknown;
}

export interface ReportIssue {
  severity: "warning" | "error";
  code: string;
  message: string;
  event_ids: number[];
  player_id: number | null;
  side: "home" | "away" | null;
  minute: number | null;
  stoppage_minute: number | null;
}

export interface ReportRenderDiagnostic {
  game_id: number;
  status: "success" | "failed";
  reused: boolean | null;
  issues: ReportIssue[];
  error: { code: string; message: string } | null;
}

export interface ReportContent {
  image: { media_type: "image/png"; base64: string } | null;
  text: { media_type: "text/plain; charset=utf-8"; content: string } | null;
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
  status: "scheduled" | "started" | "finished" | "unknown";
  report: {
    available: boolean;
    content_sha256: string | null;
    rendered_at: string | null;
  };
  updated_at: string;
}

export interface TaskMatch extends PreviewMatch {
  competition: Competition;
}

export interface Batch {
  id: number;
  batch_date: string;
  competition: Competition;
  preview_status: BatchStatus;
  headline: string;
  editors: string[];
  reviewers: string[];
  approvers: string[];
  cover: Cover;
  current_preview_article_id: number | null;
  latest_preview_article_id: number | null;
  current_report_article_id: number | null;
  latest_report_article_id: number | null;
  missing_fields: string[];
  last_error: BatchError | null;
  created_at: string;
  updated_at: string;
  weather?: Weather | null;
  matches?: PreviewMatch[];
}

export type PreviewBatch = Batch;

export interface Article {
  id: number;
  batch_id: number;
  article_type: "preview" | "report";
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

export interface ArticleCandidate {
  id: number;
  article_type: "preview" | "report";
  version_number: number;
  title: string;
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

export type AIPreviewStatus = "queued" | "running" | "succeeded" | "failed";

export interface AIPreviewModelOption {
  profile: string;
  label: string;
  estimated_seconds: number;
  score: number;
  recommended: boolean;
  available: boolean;
}

export interface AIPreviewResult {
  model_profile: string;
  status: AIPreviewStatus;
  content: string | null;
  is_stale: boolean;
  error: { code: string; message: string } | null;
  requested_at: string;
  started_at: string | null;
  finished_at: string | null;
  reused?: boolean;
}

export interface MatchManualPlayer {
  name: string;
  kit_number: number | null;
  minutes: number | null;
  description: string;
}

export interface MatchManualTeam {
  institution_name: string;
  institution_short_name: string;
  team_name: string;
  team_description: string;
  sort_basis: "minutes" | "kit_number";
  players: MatchManualPlayer[];
}

export interface AIPreviewContext {
  models: AIPreviewModelOption[];
  manual: {
    competition: Competition;
    home_team: MatchManualTeam;
    away_team: MatchManualTeam;
  };
  results: Record<string, AIPreviewResult>;
}

export interface PromptTemplate {
  key: string;
  label: string;
  content: string;
  updated_at: string;
  updated_by: string | null;
}

export interface AITitleCandidate {
  idiom_1: string;
  idiom_2: string;
  title?: string;
}

export interface AITitleResult {
  model_profile: string;
  status: AIPreviewStatus;
  candidates: AITitleCandidate[] | null;
  is_stale: boolean;
  error: { code: string; message: string } | null;
  requested_at: string;
  started_at: string | null;
  finished_at: string | null;
  reused?: boolean;
}

export interface AITitleContext {
  prefix: string;
  models: AIPreviewModelOption[];
  results: Record<string, AITitleResult>;
}

export interface InstitutionSummary {
  name: string;
  short_name: string;
  male_team_ids: number[];
  female_team_ids: number[];
  futsal_team_ids: number[];
  team_descriptions_complete: Record<Competition, boolean>;
  player_description_count: number;
  player_count: number;
}

export interface InstitutionPlayer {
  name: string;
  competitions: Competition[];
  description: string;
}

export interface InstitutionDetail {
  name: string;
  short_name: string;
  male_team_ids: number[];
  female_team_ids: number[];
  futsal_team_ids: number[];
  male_description: string;
  female_description: string;
  futsal_description: string;
  players: InstitutionPlayer[];
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
