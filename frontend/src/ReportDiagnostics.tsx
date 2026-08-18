import { Alert } from "./components";
import type { PreviewMatch, ReportIssue, ReportRenderDiagnostic } from "./types";
import { teamName } from "./utils";

const issueLabels: Record<string, string> = {
  invalid_event_ignored: "无效事件已忽略",
  duplicate_start: "球员重复出现在首发事件中",
  lineup_under_capacity: "首发人数不足",
  lineup_over_capacity: "首发人数超出限制",
  multiple_events_same_time: "同队同一时间存在多个事件，请确认顺序是否正确",
  substitution_unbalanced: "换人上下场事件数量不一致",
  substitution_order: "换人事件顺序错误",
  invalid_penalty_shootout_event: "点球大战事件类型无效",
  dismissed_player_event: "已罚下球员仍有后续事件",
  too_many_yellow_cards: "球员黄牌数量异常",
  invalid_second_yellow: "第二张黄牌缺少前序黄牌",
  too_many_red_cards: "球员红牌数量异常",
  off_player_not_on_field: "离场球员当前不在场上",
  on_player_already_on_field: "上场球员已经在场上",
  scoring_player_not_on_field: "进球队员当前不在场上",
};

function issueContext(issue: ReportIssue): string {
  const values: string[] = [];
  if (issue.side) values.push(issue.side === "home" ? "主队" : "客队");
  if (issue.minute !== null) values.push(`${issue.minute}${issue.stoppage_minute ? `+${issue.stoppage_minute}` : ""} 分钟`);
  if (issue.player_id !== null) values.push(`球员 #${issue.player_id}`);
  if (issue.event_ids.length) values.push(`事件 #${issue.event_ids.join("、#")}`);
  return values.join(" · ");
}

export function ReportDiagnostics({ diagnostics, matches = [] }: { diagnostics: ReportRenderDiagnostic[]; matches?: PreviewMatch[] }) {
  const visible = diagnostics.filter((item) => item.error || item.issues.length);
  if (!visible.length) return null;
  return (
    <div className="report-diagnostics" aria-label="渲染诊断">
      {visible.map((diagnostic) => {
        const match = matches.find((item) => item.game_id === diagnostic.game_id);
        const title = match ? `${teamName(match.home)} vs ${teamName(match.away)}` : `比赛 #${diagnostic.game_id}`;
        const hasErrors = diagnostic.status === "failed" || diagnostic.issues.some((issue) => issue.severity === "error");
        return (
          <Alert key={diagnostic.game_id} tone={hasErrors ? "danger" : "warning"}>
            <strong>{title}</strong>
            {diagnostic.error ? <span>渲染失败：{diagnostic.error.message}（{diagnostic.error.code}）</span> : null}
            {diagnostic.issues.length ? <ul className="report-issue-list">{diagnostic.issues.map((issue, index) => {
              const context = issueContext(issue);
              return <li className="report-issue" key={`${issue.code}-${index}`}><span className={`report-issue__severity report-issue__severity--${issue.severity}`}>{issue.severity === "error" ? "错误" : "警告"}</span><div><strong>{issueLabels[issue.code] ?? issue.message}</strong><span>{context ? `${context} · ` : ""}{issue.message}</span></div><code>{issue.code}</code></li>;
            })}</ul> : null}
          </Alert>
        );
      })}
    </div>
  );
}
