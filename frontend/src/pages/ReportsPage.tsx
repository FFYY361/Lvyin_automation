import { useCallback, useEffect, useState } from "react";
import { ArrowRight, ChevronDown, ChevronRight, FileText, RefreshCw, Search } from "lucide-react";
import { Link } from "react-router-dom";
import { api, errorMessage, getReportDiagnostics } from "../api";
import { useAuth } from "../auth";
import { Alert, Badge, Button, EmptyState, LoadingScreen, PageHeader, Panel } from "../components";
import { ReportDiagnostics } from "../ReportDiagnostics";
import { competitionLabels, type Article, type Competition, type PreviewBatch, type ReportRenderDiagnostic } from "../types";
import { formatDate, formatDateTime, teamName } from "../utils";

const matchStatusLabels = {
  scheduled: "未开赛",
  started: "进行中",
  finished: "已完赛",
  unknown: "状态未知",
};

function articleStatus(batch: PreviewBatch) {
  if (batch.current_report_article_id) return { label: "可预览", tone: "success" } as const;
  if (batch.latest_report_article_id) return { label: "已过期", tone: "warning" } as const;
  return { label: "未渲染", tone: "neutral" } as const;
}

export function ReportsPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [items, setItems] = useState<PreviewBatch[]>([]);
  const [details, setDetails] = useState<Record<number, PreviewBatch>>({});
  const [expanded, setExpanded] = useState<number | null>(null);
  const [date, setDate] = useState("");
  const [competition, setCompetition] = useState<Competition | "">("");
  const [showUnfinished, setShowUnfinished] = useState(false);
  const [refreshing, setRefreshing] = useState<number | null>(null);
  const [rendering, setRendering] = useState<Set<number>>(() => new Set());
  const [renderFeedback, setRenderFeedback] = useState<Record<number, { tone: "success" | "info" | "danger"; message: string; diagnostics: ReportRenderDiagnostic[] }>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    setError(null);
    const query = new URLSearchParams();
    if (date) query.set("batch_date", date);
    if (competition) query.set("competition", competition);
    try {
      const result = await api<{ items: PreviewBatch[] }>(`/api/batches${query.size ? `?${query}` : ""}`);
      setItems(result.items);
    } catch (value) {
      setError(errorMessage(value));
    } finally {
      if (!silent) setLoading(false);
    }
  }, [competition, date]);

  useEffect(() => { document.title = "战报批次 · 绿茵宣传部"; void load(); }, [load]);

  const toggle = async (batchId: number) => {
    if (expanded === batchId) {
      setExpanded(null);
      return;
    }
    setExpanded(batchId);
    setError(null);
    if (details[batchId]) return;
    try {
      const value = await api<PreviewBatch>(`/api/batches/${batchId}`);
      setDetails((current) => ({ ...current, [batchId]: value }));
    } catch (value) {
      setError(errorMessage(value));
    }
  };

  const refresh = async (batchId: number) => {
    setRefreshing(batchId);
    setError(null);
    try {
      const value = await api<PreviewBatch>(`/api/batches/${batchId}/refresh-data`, { method: "POST" });
      setDetails((current) => ({ ...current, [batchId]: value }));
      await load();
    } catch (value) {
      setError(errorMessage(value));
    } finally {
      setRefreshing(null);
    }
  };

  const renderArticle = async (batch: PreviewBatch) => {
    setRendering((current) => new Set(current).add(batch.id));
    setRenderFeedback((current) => {
      const next = { ...current };
      delete next[batch.id];
      return next;
    });
    try {
      let detail = details[batch.id];
      if (!detail) {
        detail = await api<PreviewBatch>(`/api/batches/${batch.id}`);
        setDetails((current) => ({ ...current, [batch.id]: detail }));
      }
      const result = await api<{ reused: boolean; article: Article; diagnostics: ReportRenderDiagnostic[] }>(`/api/batches/${batch.id}/render-report`, { method: "POST" });
      setRenderFeedback((current) => ({ ...current, [batch.id]: { tone: result.reused ? "info" : "success", message: result.reused ? "内容没有变化，已复用当前战报文章。" : "战报文章渲染成功。", diagnostics: result.diagnostics } }));
      await load(true);
    } catch (value) {
      const diagnostics = getReportDiagnostics(value);
      setRenderFeedback((current) => ({ ...current, [batch.id]: { tone: "danger", message: diagnostics.length ? "战报文章渲染失败，请查看逐场诊断。" : `战报文章渲染失败：${errorMessage(value)}`, diagnostics } }));
    } finally {
      setRendering((current) => {
        const next = new Set(current);
        next.delete(batch.id);
        return next;
      });
    }
  };

  return (
    <>
      <PageHeader eyebrow="内容管理" title="战报批次" description="展开批次查看比赛；比赛数据只在管理员手动重新查询时更新。" />
      <Panel className="filter-bar">
        <label><span>日期</span><input type="date" value={date} onChange={(event) => setDate(event.target.value)} /></label>
        <label><span>赛事</span><select value={competition} onChange={(event) => setCompetition(event.target.value as Competition | "")}><option value="">全部赛事</option><option value="male">男足</option><option value="female">女足</option><option value="futsal">五人制</option></select></label>
        <label className="filter-checkbox"><input type="checkbox" checked={showUnfinished} onChange={(event) => setShowUnfinished(event.target.checked)} /><span>显示未完赛比赛</span></label>
        <Button onClick={() => void load()}><Search size={16} />查询</Button>
      </Panel>
      {error ? <Alert tone="danger" onDismiss={() => setError(null)}>{error}</Alert> : null}
      {loading ? <LoadingScreen label="正在读取战报批次" /> : !items.length ? <Panel><EmptyState title="没有匹配的批次" description="请先创建对应日期和赛事的批次。" /></Panel> : (
        <Panel className="table-panel">
          <div className="data-table-wrap">
            <table className="data-table report-batch-table">
              <thead><tr><th>日期</th><th>赛事</th><th>战报文章</th><th>更新时间</th><th><span className="sr-only">操作</span></th></tr></thead>
              {items.map((batch) => {
                const detail = details[batch.id];
                const matches = (detail?.matches ?? []).filter((match) => match.active && (showUnfinished || match.status === "finished"));
                const status = articleStatus(batch);
                const feedback = renderFeedback[batch.id];
                return (
                  <tbody key={batch.id}>
                    <tr>
                      <td data-label="日期"><button className="report-batch-toggle" onClick={() => void toggle(batch.id)} aria-expanded={expanded === batch.id}>{expanded === batch.id ? <ChevronDown size={16} /> : <ChevronRight size={16} />}<span><strong>{formatDate(batch.batch_date)}</strong><small>#{batch.id}</small></span></button></td>
                      <td data-label="赛事">{competitionLabels[batch.competition]}</td>
                      <td data-label="战报文章"><Badge tone={status.tone}>{status.label}</Badge></td>
                      <td data-label="更新时间">{formatDateTime(batch.updated_at)}</td>
                      <td data-label="操作"><div className="row-actions">{isAdmin ? <><Button variant="quiet" loading={refreshing === batch.id} onClick={() => void refresh(batch.id)} title="重新查询" aria-label="重新查询"><RefreshCw size={16} /></Button><Button variant="quiet" loading={rendering.has(batch.id)} onClick={() => void renderArticle(batch)} title="渲染战报文章" aria-label="渲染文章"><FileText size={16} /></Button></> : null}<Link className="icon-link" to={`/reports/${batch.id}/article`} aria-label="预览战报文章" title="预览战报文章"><ArrowRight size={17} /></Link></div></td>
                    </tr>
                    {feedback ? <tr className="batch-feedback-row"><td colSpan={5}><Alert tone={feedback.tone} onDismiss={() => setRenderFeedback((current) => { const next = { ...current }; delete next[batch.id]; return next; })}>{feedback.message}</Alert><ReportDiagnostics diagnostics={feedback.diagnostics} matches={detail?.matches ?? []} /></td></tr> : null}
                    {expanded === batch.id ? <tr className="report-expanded-row"><td colSpan={5}>{!detail ? <LoadingScreen label="正在读取比赛" /> : !matches.length ? <EmptyState title="没有可显示的比赛" description={showUnfinished ? "该批次当前没有有效比赛。" : "当前没有已完赛比赛，可勾选显示未完赛比赛。"} /> : <div className="match-card-list">{matches.map((match) => <Link className="match-card" key={match.game_id} to={`/reports/${batch.id}/matches/${match.game_id}`}><div className="match-card__heading"><span>{match.competition_name} · {match.stage}</span><Badge tone={match.status === "finished" ? "success" : "neutral"}>{matchStatusLabels[match.status]}</Badge></div><strong>{teamName(match.home)} <em>vs</em> {teamName(match.away)}</strong><div className="match-card__meta"><span>{formatDateTime(match.kickoff)}</span><span>{match.venue}</span><span>{match.report.available ? "战报已生成" : "尚未生成战报"}</span></div><ChevronRight className="match-card__arrow" size={18} /></Link>)}</div>}</td></tr> : null}
                  </tbody>
                );
              })}
            </table>
          </div>
        </Panel>
      )}
    </>
  );
}
