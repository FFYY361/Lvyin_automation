import { useCallback, useEffect, useState } from "react";
import { ArrowLeft, RefreshCw } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { api, errorMessage, getReportDiagnostics } from "../api";
import { Alert, Button, EmptyState, LoadingScreen, PageHeader, Panel, SectionTitle } from "../components";
import { ReportDiagnostics } from "../ReportDiagnostics";
import { competitionLabels, type PreviewBatch, type PreviewMatch, type ReportContent, type ReportRenderDiagnostic } from "../types";
import { formatDateTime, teamName } from "../utils";

export function ReportMatchPage() {
  const { batchId, gameId } = useParams();
  const [batch, setBatch] = useState<PreviewBatch | null>(null);
  const [match, setMatch] = useState<PreviewMatch | null>(null);
  const [content, setContent] = useState<ReportContent | null>(null);
  const [loading, setLoading] = useState(true);
  const [rendering, setRendering] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [diagnostics, setDiagnostics] = useState<ReportRenderDiagnostic[]>([]);

  const loadContent = useCallback(async (value: PreviewMatch) => {
    if (!value.report.available) { setContent(null); return; }
    setContent(await api<ReportContent>(`/api/matches/${value.game_id}/report/content`));
  }, []);

  const load = useCallback(async () => {
    if (!batchId || !gameId) return;
    setLoading(true); setError(null);
    try {
      const value = await api<PreviewBatch>(`/api/batches/${batchId}`);
      const selected = (value.matches ?? []).find((item) => item.game_id === Number(gameId));
      if (!selected) throw new Error("比赛不存在");
      setBatch(value); setMatch(selected); await loadContent(selected);
    } catch (value) { setError(errorMessage(value)); } finally { setLoading(false); }
  }, [batchId, gameId, loadContent]);

  useEffect(() => { document.title = "单场战报 · 绿茵宣传部"; void load(); }, [load]);

  const render = async () => {
    if (!match) return;
    setRendering(true); setError(null); setMessage(null); setDiagnostics([]);
    try {
      const result = await api<{ reused: boolean; match: PreviewMatch; diagnostics: ReportRenderDiagnostic[] }>(`/api/matches/${match.game_id}/render-report`, { method: "POST" });
      setMatch(result.match); setMessage(result.reused ? "比赛数据没有变化，已复用现有战报。" : "已生成新的单场战报。");
      setDiagnostics(result.diagnostics);
      await loadContent(result.match);
    } catch (value) { setError(errorMessage(value)); setDiagnostics(getReportDiagnostics(value)); } finally { setRendering(false); }
  };

  if (loading) return <LoadingScreen label="正在读取单场战报" />;
  if (!batch || !match) return <><PageHeader title="单场战报" actions={<Link className="button button--quiet" to="/reports"><ArrowLeft size={16} />返回战报列表</Link>} /><Alert tone="danger">{error || "比赛不存在"}</Alert></>;
  return <>
    <PageHeader eyebrow={`${batch.batch_date} · ${competitionLabels[batch.competition]}`} title={`${teamName(match.home)} vs ${teamName(match.away)}`} description={`${match.competition_name} · ${match.stage} · ${formatDateTime(match.kickoff)}`} actions={<><Link className="button button--quiet" to="/reports"><ArrowLeft size={16} />返回战报列表</Link><Button variant="primary" loading={rendering} onClick={() => void render()}><RefreshCw size={16} />{match.report.available ? "重新渲染" : "渲染战报"}</Button></>} />
    {error ? <Alert tone="danger" onDismiss={() => setError(null)}>{error}</Alert> : null}
    {message ? <Alert tone="success" onDismiss={() => setMessage(null)}>{message}</Alert> : null}
    <ReportDiagnostics diagnostics={diagnostics} matches={[match]} />
    {match.status !== "finished" ? <Alert tone="warning">数据库中的比赛状态尚未完赛；渲染时仍会实时查询，必要时请管理员先重新查询批次。</Alert> : null}
    <Panel>
      <SectionTitle title="战报效果" description={match.report.rendered_at ? `生成于 ${formatDateTime(match.report.rendered_at)}` : "尚未生成"} />
      {!match.report.available ? <EmptyState title="尚未生成战报" description="点击渲染战报后将实时查询当前比赛事件。" /> : !content ? <LoadingScreen label="正在读取战报内容" /> : <>{content.image ? <img src={`data:${content.image.media_type};base64,${content.image.base64}`} alt={`${teamName(match.home)} 对阵 ${teamName(match.away)} 战报`} style={{ display: "block", width: "100%", height: "auto" }} /> : null}{content.text ? <p className="panel-copy">{content.text.content}</p> : null}</>}
    </Panel>
  </>;
}
