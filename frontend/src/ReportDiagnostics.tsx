import { Alert } from "./components";
import type { PreviewMatch, ReportIssue, ReportRenderDiagnostic } from "./types";
import { teamName } from "./utils";

function issueMessage(issue: ReportIssue, match?: PreviewMatch): string {
  const team = issue.team_name ?? (match
    ? issue.side ? teamName(issue.side === "home" ? match.home : match.away)
      : `${teamName(match.home)} vs ${teamName(match.away)}`
    : null);
  return [team, issue.time_label, issue.message].filter(Boolean).join(" · ");
}

export function reportBatchFeedback(diagnostics: ReportRenderDiagnostic[]) {
  const warningCount = diagnostics.filter((item) => item.issues.some((issue) => issue.severity === "warning")).length;
  const failedCount = diagnostics.filter((item) => item.status === "failed").length;
  const successCount = diagnostics.filter((item) => item.status === "success").length;
  return {
    tone: failedCount ? "danger" as const : "warning" as const,
    message: `${successCount ? "单场战报已生成并保存" : "本批次单场战报未生成"}；${warningCount ? `有${warningCount}场比赛需要核对，` : ""}${failedCount ? `有${failedCount}场比赛生成失败，` : ""}暂未生成批次文章。`,
  };
}

export function ReportDiagnostics({ diagnostics, matches = [] }: { diagnostics: ReportRenderDiagnostic[]; matches?: PreviewMatch[] }) {
  return (
    <div className="report-diagnostics" aria-label="渲染诊断">
      {diagnostics.map((diagnostic) => {
        const match = matches.find((item) => item.game_id === diagnostic.game_id);
        return <div key={diagnostic.game_id}>
          {diagnostic.error ? <Alert tone="danger"><strong>{match ? `${teamName(match.home)} vs ${teamName(match.away)}` : `比赛 #${diagnostic.game_id}`}</strong><span>渲染失败：{diagnostic.error.message}（{diagnostic.error.code}）</span></Alert> : null}
          {diagnostic.issues.filter((issue) => issue.code !== "invalid_event_ignored").map((issue, index) =>
            <Alert key={`${issue.code}-${index}`} tone={issue.severity === "info" ? "info" : issue.severity === "warning" ? "warning" : "danger"}>
              <strong>[{issue.severity === "info" ? "信息" : issue.severity === "warning" ? "警告" : "错误"}] {issue.code}</strong>
              <span className="report-issue-message">{issueMessage(issue, match)}</span>
            </Alert>,
          )}
        </div>;
      })}
    </div>
  );
}
