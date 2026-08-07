export function formatDate(value: string): string {
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "long",
    day: "numeric",
    weekday: "short",
  }).format(date);
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

export function parseNames(value: string): string[] {
  return Array.from(
    new Set(
      value
        .split(/[、，,\n]/)
        .map((item) => item.trim())
        .filter(Boolean),
    ),
  );
}

export function namesText(values: string[]): string {
  return values.join("、");
}

export function cartesianPairs<T, U>(left: T[], right: U[]): Array<{ left: T; right: U }> {
  return left.flatMap((leftValue) => right.map((rightValue) => ({ left: leftValue, right: rightValue })));
}

export function futureMatchDates(weekCount: number, from = new Date()): string[] {
  if (!Number.isInteger(weekCount) || weekCount <= 0) return [];
  const cursor = new Date(from.getFullYear(), from.getMonth(), from.getDate());
  const dates: string[] = [];
  for (let offset = 0; offset < weekCount * 7; offset += 1) {
    if ([0, 4, 6].includes(cursor.getDay())) {
      const year = cursor.getFullYear();
      const month = String(cursor.getMonth() + 1).padStart(2, "0");
      const day = String(cursor.getDate()).padStart(2, "0");
      dates.push(`${year}-${month}-${day}`);
    }
    cursor.setDate(cursor.getDate() + 1);
  }
  return dates;
}

export function moveItem<T>(values: T[], from: number, to: number): T[] {
  if (from < 0 || from >= values.length || to < 0 || to >= values.length || from === to) return values;
  const result = [...values];
  const [item] = result.splice(from, 1);
  result.splice(to, 0, item);
  return result;
}

export function teamName(value: TeamRefSnapshot): string {
  return value.short_name || value.name;
}

export function formatPlayedMatch(value: PlayedMatchSnapshot, includeContext = false): string {
  const result = `${teamName(value.home)} ${value.result_text} ${teamName(value.away)}`;
  if (!includeContext) return result;
  const context = [value.season, value.competition_label, value.stage].filter(Boolean).join(" · ");
  return context ? `${context}｜${result}` : result;
}

export function formatSeasonOutcome(value: SeasonOutcomeSnapshot): string {
  const competition = value.competition_label ? ` · ${value.competition_label}` : "";
  return `${value.season}${competition}｜${value.outcome}`;
}

export function matchTaskStatus(match: Pick<PreviewMatch, "active" | "task_open" | "claimed_by_user_id">): { label: string; tone: string } {
  if (!match.active) return { label: "已失效", tone: "neutral" };
  if (!match.task_open) return { label: "未开放", tone: "neutral" };
  if (match.claimed_by_user_id !== null) return { label: "开放 · 已领取", tone: "success" };
  return { label: "开放 · 未领取", tone: "info" };
}
import type { PlayedMatchSnapshot, PreviewMatch, SeasonOutcomeSnapshot, TeamRefSnapshot } from "./types";
