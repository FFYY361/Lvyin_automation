import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowLeft, ChevronLeft, ChevronRight, Save } from "lucide-react";
import { Link, useBlocker, useParams } from "react-router-dom";
import { ApiError, api, errorMessage, jsonBody } from "../api";
import { Alert, Badge, Button, Field, LoadingScreen, Modal, NameInput, PageHeader, Panel, SectionTitle } from "../components";
import { competitionLabels, type PlayedMatchSnapshot, type PreviewBatch, type PreviewMatch, type SeasonOutcomeSnapshot } from "../types";
import { formatDateTime, formatPlayedMatch, formatSeasonOutcome, matchTaskStatus, namesText, parseNames, teamName } from "../utils";

interface ConflictValue { body_version: number; writers: string[]; body: string }

function HistoryList({ values, empty, render }: { values: Array<PlayedMatchSnapshot | SeasonOutcomeSnapshot>; empty: string; render: (value: PlayedMatchSnapshot | SeasonOutcomeSnapshot) => string }) {
  if (!values.length) return <p className="history-empty">{empty}</p>;
  return <ul>{values.map((value, index) => <li key={"game_id" in value ? value.game_id : `${value.season}-${index}`}>{render(value)}</li>)}</ul>;
}

export function MatchPage() {
  const { batchId, gameId } = useParams();
  const [batch, setBatch] = useState<PreviewBatch | null>(null);
  const [match, setMatch] = useState<PreviewMatch | null>(null);
  const [writers, setWriters] = useState("");
  const [body, setBody] = useState("");
  const [baseWriters, setBaseWriters] = useState<string[]>([]);
  const [baseBody, setBaseBody] = useState("");
  const [version, setVersion] = useState(0);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [conflict, setConflict] = useState<ConflictValue | null>(null);
  const parsedWriters = useMemo(() => parseNames(writers), [writers]);
  const dirty = body !== baseBody || JSON.stringify(parsedWriters) !== JSON.stringify(baseWriters);
  const blocker = useBlocker(dirty);

  const applyMatch = useCallback((value: PreviewMatch) => {
    setMatch(value);
    setWriters(namesText(value.writers));
    setBody(value.body);
    setBaseWriters(value.writers);
    setBaseBody(value.body);
    setVersion(value.body_version);
  }, []);

  const load = useCallback(async () => {
    if (!batchId || !gameId) return;
    setLoading(true); setError(null); setSuccess(null);
    try {
      const value = await api<PreviewBatch>(`/api/preview-batches/${batchId}`);
      const selected = value.matches?.find((item) => item.game_id === Number(gameId));
      setBatch(value);
      if (!selected) {
        setMatch(null);
        setError("该比赛不属于当前批次，或比赛数据已不存在。");
      } else {
        applyMatch(selected);
      }
    } catch (value) {
      setError(errorMessage(value));
    } finally {
      setLoading(false);
    }
  }, [applyMatch, batchId, gameId]);

  useEffect(() => { document.title = "比赛写作 · 前瞻管理"; void load(); }, [load]);
  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => { if (dirty) { event.preventDefault(); event.returnValue = ""; } };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [dirty]);

  const save = async () => {
    if (!match) return;
    setSaving(true); setError(null); setSuccess(null);
    try {
      const result = await api<{ game_id: number; writers: string[]; body: string; body_version: number }>(`/api/preview-matches/${match.game_id}`, { method: "PATCH", ...jsonBody({ expected_version: version, writers: parsedWriters, body }) });
      setWriters(namesText(result.writers)); setBody(result.body); setBaseWriters(result.writers); setBaseBody(result.body); setVersion(result.body_version);
      setMatch({ ...match, writers: result.writers, body: result.body, body_version: result.body_version });
      setSuccess("正文与署名已保存。");
    } catch (value) {
      if (value instanceof ApiError && value.status === 409 && value.code === "body_version_conflict") {
        const details = value.details as unknown as ConflictValue;
        if (typeof details?.body_version === "number") setConflict(details);
      } else {
        setError(errorMessage(value));
      }
    } finally {
      setSaving(false);
    }
  };

  const loadServer = () => {
    if (!conflict) return;
    setWriters(namesText(conflict.writers)); setBody(conflict.body); setBaseWriters(conflict.writers); setBaseBody(conflict.body); setVersion(conflict.body_version); setConflict(null);
  };
  const rebaseLocal = () => {
    if (!conflict) return;
    setBaseWriters(conflict.writers); setBaseBody(conflict.body); setVersion(conflict.body_version); setConflict(null);
  };

  if (loading && !match) return <LoadingScreen label="正在读取比赛详情" />;
  if (!batch || !match) return <><PageHeader title="比赛不存在" actions={batchId ? <Link className="button button--quiet" to={`/batches/${batchId}`}><ArrowLeft size={16} />返回批次</Link> : undefined} /><Alert tone="danger">{error || "无法读取比赛"}</Alert></>;

  const matches = batch.matches ?? [];
  const index = matches.findIndex((item) => item.game_id === match.game_id);
  const previous = index > 0 ? matches[index - 1] : null;
  const next = index >= 0 && index < matches.length - 1 ? matches[index + 1] : null;
  const status = matchTaskStatus(match);
  const home = teamName(match.home);
  const away = teamName(match.away);
  const navigation = (target: PreviewMatch | null, direction: "previous" | "next") => target
    ? <Link className="button button--quiet" to={`/batches/${batch.id}/matches/${target.game_id}`}>{direction === "previous" ? <ChevronLeft size={16} /> : null}{direction === "previous" ? "上一场" : "下一场"}{direction === "next" ? <ChevronRight size={16} /> : null}</Link>
    : <span className="button button--quiet button--disabled" aria-disabled="true">{direction === "previous" ? <ChevronLeft size={16} /> : null}{direction === "previous" ? "上一场" : "下一场"}{direction === "next" ? <ChevronRight size={16} /> : null}</span>;

  return (
    <>
      <PageHeader eyebrow={`${batch.preview_date} · ${competitionLabels[batch.competition]}`} title={`${home} vs ${away}`} description={`${match.competition_name} · ${match.stage}`} actions={<><Link className="button button--quiet" to={`/batches/${batch.id}`}><ArrowLeft size={16} />返回批次</Link>{navigation(previous, "previous")}{navigation(next, "next")}</>} />
      {error ? <Alert tone="danger" onDismiss={() => setError(null)}>{error}</Alert> : null}
      {success ? <Alert tone="success" onDismiss={() => setSuccess(null)}>{success}</Alert> : null}

      <Panel className="match-overview">
        <div><span>任务状态</span><Badge tone={status.tone}>{status.label}</Badge></div>
        <div><span>开球时间</span><strong>{formatDateTime(match.kickoff)}</strong></div>
        <div><span>比赛场地</span><strong>{match.venue}</strong></div>
        <div><span>比赛 ID</span><strong>#{match.game_id}</strong></div>
      </Panel>

      <Panel className="match-writing-panel">
        <SectionTitle title="正文与署名" description="一个或多个换行都会分段，段前空格会自动去除。" />
        <div className="writer-grid">
          <Field label="署名"><NameInput value={writers} onChange={setWriters} /></Field>
          <div className="version-display"><span>保存版本</span><strong>v{version}</strong>{dirty ? <Badge tone="warning">未保存</Badge> : <Badge tone="success">已保存</Badge>}</div>
        </div>
        <Field label="前瞻正文"><textarea rows={14} value={body} onChange={(event) => setBody(event.target.value)} placeholder="粘贴或填写本场比赛的前瞻正文……" /></Field>
        <div className="editor-actions"><Button disabled={!dirty} onClick={() => { setWriters(namesText(baseWriters)); setBody(baseBody); }}>撤销修改</Button><Button variant="primary" loading={saving} disabled={!dirty} onClick={() => void save()}><Save size={16} />保存正文</Button></div>
      </Panel>

      <Panel className="match-history-panel">
        <SectionTitle title="球队战绩与交锋" description="过往三届成绩、本届已完成比赛及两队近三届交锋。" />
        <div className="history-team-grid">
          {[match.home, match.away].map((team) => (
            <article className="history-team-card" key={team.team_id}>
              <h3>{teamName(team)}</h3>
              <div className="history-section"><strong>过往三届战绩</strong><HistoryList values={team.previous_outcomes} empty="暂无" render={(value) => formatSeasonOutcome(value as SeasonOutcomeSnapshot)} /></div>
              <div className="history-section"><strong>本届赛果</strong><HistoryList values={team.current_results} empty="暂无" render={(value) => formatPlayedMatch(value as PlayedMatchSnapshot)} /></div>
            </article>
          ))}
        </div>
        <div className="head-to-head-card"><strong>近三届交锋</strong><HistoryList values={match.head_to_head} empty="无" render={(value) => formatPlayedMatch(value as PlayedMatchSnapshot, true)} /></div>
      </Panel>

      {conflict ? <Modal title="正文已被其他请求更新" wide actions={<><Button onClick={loadServer}>加载服务器版本</Button><Button variant="primary" onClick={rebaseLocal}>保留本地内容并人工合并</Button></>}>
        <Alert tone="warning">服务器版本已经变为 v{conflict.body_version}。系统不会自动覆盖，请比较后明确选择。</Alert>
        <div className="conflict-grid"><div><strong>你的未保存内容</strong><span>署名：{writers || "—"}</span><pre>{body || "（空正文）"}</pre></div><div><strong>服务器当前内容</strong><span>署名：{namesText(conflict.writers) || "—"}</span><pre>{conflict.body || "（空正文）"}</pre></div></div>
      </Modal> : null}
      {blocker.state === "blocked" ? <Modal title="有未保存的正文" actions={<><Button onClick={() => blocker.reset()}>留在此页</Button><Button variant="danger" onClick={() => blocker.proceed()}>放弃修改并离开</Button></>}><p>离开页面会丢失尚未保存的署名或正文。</p></Modal> : null}
    </>
  );
}
