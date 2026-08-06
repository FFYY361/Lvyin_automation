import { useCallback, useEffect, useState } from "react";
import { ArrowLeft, RefreshCw } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { api, errorMessage } from "../api";
import { Alert, Badge, Button, EmptyState, LoadingScreen, PageHeader, Panel, SectionTitle } from "../components";
import { competitionLabels, type PreviewBatch, type PreviewMatch } from "../types";
import { formatDateTime, teamName } from "../utils";

export function ReportMatchPage() {
  const { batchId, gameId } = useParams();
  const [batch, setBatch] = useState<PreviewBatch | null>(null);
  const [match, setMatch] = useState<PreviewMatch | null>(null);
  const [textContent, setTextContent] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [rendering, setRendering] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadContent = useCallback(async (value: PreviewMatch) => {
    if (!value.report.available || value.report.kind !== "text") { setTextContent(null); return; }
    const response = await fetch(`/api/matches/${value.game_id}/report/content`, { credentials: "include" });
    if (!response.ok) throw new Error("无法读取战报内容");
    setTextContent(await response.text());
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

  useEffect(() => { document.title = "单场战报 · 绿茵管理"; void load(); }, [load]);

  const render = async () => {
    if (!match) return;
    setRendering(true); setError(null); setMessage(null);
    try {
      const result = await api<{ reused: boolean; match: PreviewMatch }>(`/api/matches/${match.game_id}/render-report`, { method: "POST" });
      setMatch(result.match); setMessage(result.reused ? "比赛数据没有变化，已复用现有战报。" : "已生成新的单场战报。");
      await loadContent(result.match);
    } catch (value) { setError(errorMessage(value)); } finally { setRendering(false); }
  };

  if (loading) return <LoadingScreen label="正在读取单场战报" />;
  if (!batch || !match) return <><PageHeader title="单场战报" actions={<Link className="button button--quiet" to="/reports"><ArrowLeft size={16} />返回战报列表</Link>} /><Alert tone="danger">{error || "比赛不存在"}</Alert></>;
  return <>
    <PageHeader eyebrow={`${batch.batch_date} · ${competitionLabels[batch.competition]}`} title={`${teamName(match.home)} vs ${teamName(match.away)}`} description={`${match.competition_name} · ${match.stage} · ${formatDateTime(match.kickoff)}`} actions={<><Link className="button button--quiet" to="/reports"><ArrowLeft size={16} />返回战报列表</Link><Button variant="primary" loading={rendering} onClick={() => void render()}><RefreshCw size={16} />{match.report.available ? "重新渲染" : "渲染战报"}</Button></>} />
    {error ? <Alert tone="danger" onDismiss={() => setError(null)}>{error}</Alert> : null}
    {message ? <Alert tone="success" onDismiss={() => setMessage(null)}>{message}</Alert> : null}
    {match.status !== "finished" ? <Alert tone="warning">数据库中的比赛状态尚未完赛；渲染时仍会实时查询，必要时请管理员先重新查询批次。</Alert> : null}
    <Panel>
      <SectionTitle title="战报效果" description={match.report.rendered_at ? `生成于 ${formatDateTime(match.report.rendered_at)}` : "尚未生成"} />
      {!match.report.available ? <EmptyState title="尚未生成战报" description="点击渲染战报后将实时查询当前比赛事件。" /> : match.report.kind === "image" ? <img src={`/api/matches/${match.game_id}/report/content?v=${match.report.content_sha256}`} alt={`${teamName(match.home)} 对阵 ${teamName(match.away)} 战报`} style={{ display: "block", width: "100%", height: "auto" }} /> : <p className="panel-copy">{textContent ?? "正在读取弃赛说明…"}</p>}
    </Panel>
  </>;
}
