import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowLeft, ChevronLeft, ChevronRight, Copy, Save, Sparkles } from "lucide-react";
import { Link, useBlocker, useParams } from "react-router-dom";
import { ApiError, api, errorMessage, jsonBody } from "../api";
import { useAuth } from "../auth";
import { Alert, Badge, Button, Field, LoadingScreen, Modal, NameInput, PageHeader, Panel, SectionTitle } from "../components";
import {
  competitionLabels,
  type AIPreviewContext,
  type AIPreviewResult,
  type MatchManualTeam,
  type PlayedMatchSnapshot,
  type PreviewBatch,
  type PreviewMatch,
  type SeasonOutcomeSnapshot,
} from "../types";
import { formatDateTime, formatPlayedMatch, formatSeasonOutcome, matchTaskStatus, namesText, parseNames, teamName } from "../utils";

interface ConflictValue { body_version: number; writers: string[]; body: string }
type ManualSide = "home_team" | "away_team";

function HistoryList({ values, empty, render }: { values: Array<PlayedMatchSnapshot | SeasonOutcomeSnapshot>; empty: string; render: (value: PlayedMatchSnapshot | SeasonOutcomeSnapshot) => string }) {
  if (!values.length) return <p className="history-empty">{empty}</p>;
  return <ul>{values.map((value, index) => <li key={"game_id" in value ? value.game_id : `${value.season}-${index}`}>{render(value)}</li>)}</ul>;
}

function ManualTeamEditor({ side, value, onTeamChange, onPlayerChange }: {
  side: "主队" | "客队";
  value: MatchManualTeam;
  onTeamChange: (value: string) => void;
  onPlayerChange: (name: string, value: string) => void;
}) {
  return (
    <article className="manual-team-card">
      <header>
        <div><span>{side}</span><h3>{value.team_name}</h3></div>
        <Badge>{value.institution_short_name}</Badge>
      </header>
      <Field label="球队描述" hint="只填写风格、人员特点等人工资料，不必重复战绩数据。">
        <textarea rows={6} value={value.team_description} onChange={(event) => onTeamChange(event.target.value)} />
      </Field>
      <div className="manual-player-heading">
        <strong>球员描述</strong>
        <span>{value.sort_basis === "minutes" ? "按本赛季出场时间排序" : "按球衣号码排序"}</span>
      </div>
      <div className="manual-player-list">
        {value.players.map((player) => (
          <div className="manual-player-row" key={player.name}>
            <div className="manual-player-meta">
              <strong>{player.kit_number ?? "—"}</strong>
              <span>{player.name}</span>
              <small>{typeof player.minutes === "number" ? `${player.minutes} 分钟` : "出场时间未知"}</small>
            </div>
            <textarea aria-label={`${player.name}的描述`} rows={3} value={player.description} onChange={(event) => onPlayerChange(player.name, event.target.value)} />
          </div>
        ))}
      </div>
    </article>
  );
}

export function MatchPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const { batchId, gameId } = useParams();
  const [batch, setBatch] = useState<PreviewBatch | null>(null);
  const [match, setMatch] = useState<PreviewMatch | null>(null);
  const [writers, setWriters] = useState("");
  const [body, setBody] = useState("");
  const [baseWriters, setBaseWriters] = useState<string[]>([]);
  const [baseBody, setBaseBody] = useState("");
  const [version, setVersion] = useState(0);
  const [aiContext, setAIContext] = useState<AIPreviewContext | null>(null);
  const [manual, setManual] = useState<AIPreviewContext["manual"] | null>(null);
  const [baseManual, setBaseManual] = useState<AIPreviewContext["manual"] | null>(null);
  const [selectedModel, setSelectedModel] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [savingManual, setSavingManual] = useState(false);
  const [startingAI, setStartingAI] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [conflict, setConflict] = useState<ConflictValue | null>(null);
  const [accessDenied, setAccessDenied] = useState(false);
  const parsedWriters = useMemo(() => parseNames(writers), [writers]);
  const bodyDirty = body !== baseBody || (isAdmin && JSON.stringify(parsedWriters) !== JSON.stringify(baseWriters));
  const manualDirty = manual !== null && baseManual !== null && JSON.stringify(manual) !== JSON.stringify(baseManual);
  const dirty = bodyDirty || manualDirty;
  const blocker = useBlocker(dirty);

  const applyMatch = useCallback((value: PreviewMatch) => {
    setMatch(value);
    setWriters(namesText(value.writers));
    setBody(value.body);
    setBaseWriters(value.writers);
    setBaseBody(value.body);
    setVersion(value.body_version);
  }, []);

  const applyAIContext = useCallback((value: AIPreviewContext, replaceManual = true) => {
    setAIContext(value);
    setSelectedModel((current) => {
      if (value.models.some((model) => model.profile === current)) return current;
      return value.models.find((model) => model.recommended && model.available)?.profile
        ?? value.models.find((model) => model.available)?.profile
        ?? value.models.find((model) => model.recommended)?.profile
        ?? value.models[0]?.profile
        ?? "";
    });
    if (replaceManual) {
      setManual(value.manual);
      setBaseManual(value.manual);
    }
  }, []);

  const refreshAIContext = useCallback(async (replaceManual = true) => {
    if (!gameId) return;
    const value = await api<AIPreviewContext>(`/api/matches/${gameId}/ai-preview-context`);
    if (!Array.isArray(value?.models) || !value.manual || !value.results) {
      throw new Error("AI 写作资料返回格式无效");
    }
    applyAIContext(value, replaceManual);
  }, [applyAIContext, gameId]);

  const load = useCallback(async () => {
    if (!batchId || !gameId) return;
    setLoading(true); setError(null); setSuccess(null);
    try {
      const value = await api<PreviewBatch>(`/api/batches/${batchId}`);
      const selected = value.matches?.find((item) => item.game_id === Number(gameId));
      setBatch(value);
      if (!selected) {
        setMatch(null);
        setError("该比赛不属于当前批次，或比赛数据已不存在。");
      } else if (!isAdmin && selected.claimed_by_user_id !== user?.id) {
        setMatch(null); setAccessDenied(true);
        setError("普通用户只能进入本人已经认领的比赛。");
      } else {
        setAccessDenied(false);
        applyMatch(selected);
        await refreshAIContext();
      }
    } catch (value) {
      setError(errorMessage(value));
    } finally {
      setLoading(false);
    }
  }, [applyMatch, batchId, gameId, isAdmin, refreshAIContext, user?.id]);

  useEffect(() => { document.title = "比赛写作 · 绿茵宣传部"; void load(); }, [load]);
  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => { if (dirty) { event.preventDefault(); event.returnValue = ""; } };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [dirty]);

  const selectedResult = aiContext?.results[selectedModel];
  useEffect(() => {
    if (!gameId || !selectedModel || !selectedResult || !["queued", "running"].includes(selectedResult.status)) return;
    let active = true;
    const poll = async () => {
      try {
        const value = await api<AIPreviewResult>(`/api/matches/${gameId}/ai-preview-results/${encodeURIComponent(selectedModel)}`);
        if (!active) return;
        setAIContext((current) => current ? { ...current, results: { ...current.results, [selectedModel]: value } } : current);
        if (!["queued", "running"].includes(value.status)) await refreshAIContext(!manualDirty);
      } catch (value) {
        if (active) setError(errorMessage(value));
      }
    };
    const timer = window.setInterval(() => { void poll(); }, 2500);
    return () => { active = false; window.clearInterval(timer); };
  }, [gameId, manualDirty, refreshAIContext, selectedModel, selectedResult]);

  const save = async () => {
    if (!match) return;
    setSaving(true); setError(null); setSuccess(null);
    try {
      const result = await api<{ game_id: number; writers: string[]; body: string; body_version: number }>(`/api/matches/${match.game_id}${isAdmin ? "" : "/body"}`, { method: "PATCH", ...jsonBody(isAdmin ? { expected_version: version, writers: parsedWriters, body } : { expected_version: version, body }) });
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

  const startGeneration = async () => {
    if (!match || !selectedModel) return;
    setStartingAI(true); setError(null); setSuccess(null); setCopied(false);
    try {
      const result = await api<AIPreviewResult>(`/api/matches/${match.game_id}/ai-preview-generations`, { method: "POST", ...jsonBody({ model_profile: selectedModel }) });
      setAIContext((current) => current ? { ...current, results: { ...current.results, [selectedModel]: result } } : current);
      if (result.status === "succeeded") setSuccess(result.reused ? "已载入此前生成的前瞻。" : "AI 前瞻已生成。");
    } catch (value) {
      setError(errorMessage(value));
    } finally {
      setStartingAI(false);
    }
  };

  const copyResult = async () => {
    if (!selectedResult?.content) return;
    try {
      await navigator.clipboard.writeText(selectedResult.content);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      setError("无法访问剪贴板，请手动选择正文复制。");
    }
  };

  const updateManualTeam = (side: ManualSide, value: string) => {
    setManual((current) => current ? { ...current, [side]: { ...current[side], team_description: value } } : current);
  };
  const updateManualPlayer = (side: ManualSide, name: string, value: string) => {
    setManual((current) => current ? { ...current, [side]: { ...current[side], players: current[side].players.map((player) => player.name === name ? { ...player, description: value } : player) } } : current);
  };
  const saveManual = async () => {
    if (!match || !manual) return;
    setSavingManual(true); setError(null); setSuccess(null);
    const sidePayload = (team: MatchManualTeam) => ({ team_description: team.team_description, player_descriptions: Object.fromEntries(team.players.map((player) => [player.name, player.description])) });
    try {
      const value = await api<AIPreviewContext["manual"]>(`/api/matches/${match.game_id}/manual-descriptions`, { method: "PUT", ...jsonBody({ home_team: sidePayload(manual.home_team), away_team: sidePayload(manual.away_team) }) });
      setManual(value); setBaseManual(value);
      await refreshAIContext();
      setSuccess("双方人工描述已保存，已有 AI 正文已重新检查有效性。");
    } catch (value) {
      setError(errorMessage(value));
    } finally {
      setSavingManual(false);
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
  if (!batch || !match) return <><PageHeader title={accessDenied ? "无法进入比赛" : "比赛不存在"} actions={batchId ? <Link className="button button--quiet" to={`/previews/${batchId}`}><ArrowLeft size={16} />返回批次</Link> : undefined} /><Alert tone="danger">{error || "无法读取比赛"}</Alert></>;

  const matches = isAdmin ? batch.matches ?? [] : (batch.matches ?? []).filter((item) => item.claimed_by_user_id === user?.id);
  const index = matches.findIndex((item) => item.game_id === match.game_id);
  const previous = index > 0 ? matches[index - 1] : null;
  const next = index >= 0 && index < matches.length - 1 ? matches[index + 1] : null;
  const status = matchTaskStatus(match);
  const home = teamName(match.home);
  const away = teamName(match.away);
  const selectedModelOption = aiContext?.models.find((model) => model.profile === selectedModel);
  const aiRunning = selectedResult?.status === "queued" || selectedResult?.status === "running";
  const navigation = (target: PreviewMatch | null, direction: "previous" | "next") => target
    ? <Link className="button button--quiet" to={`/previews/${batch.id}/matches/${target.game_id}`}>{direction === "previous" ? <ChevronLeft size={16} /> : null}{direction === "previous" ? "上一场" : "下一场"}{direction === "next" ? <ChevronRight size={16} /> : null}</Link>
    : <span className="button button--quiet button--disabled" aria-disabled="true">{direction === "previous" ? <ChevronLeft size={16} /> : null}{direction === "previous" ? "上一场" : "下一场"}{direction === "next" ? <ChevronRight size={16} /> : null}</span>;

  return (
    <>
      <PageHeader eyebrow={`${batch.batch_date} · ${competitionLabels[batch.competition]}`} title={`${home} vs ${away}`} description={`${match.competition_name} · ${match.stage}`} actions={<><Link className="button button--quiet" to={`/previews/${batch.id}`}><ArrowLeft size={16} />返回批次</Link>{navigation(previous, "previous")}{navigation(next, "next")}</>} />
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
          <Field label="署名" htmlFor="match-writers">{isAdmin ? <NameInput id="match-writers" value={writers} onChange={setWriters} /> : <input id="match-writers" value={writers} readOnly aria-readonly="true" />}</Field>
          <div className="version-display"><span>保存序号</span><strong>#{version}</strong>{bodyDirty ? <Badge tone="warning">未保存</Badge> : <Badge tone="success">已保存</Badge>}</div>
        </div>
        <Field label="前瞻正文" htmlFor="match-body"><textarea id="match-body" rows={14} value={body} onChange={(event) => setBody(event.target.value)} placeholder="粘贴或填写本场比赛的前瞻正文……" /></Field>
        <Alert tone="info">
          <strong>写作建议</strong>
          <span>建议采用三段式结构：先介绍主队，再介绍客队，最后自然收束到本场对决。</span>
          <span>除决赛外，正文不应出现球员真实姓名。</span>
          <span>措辞应克制、客观，避免强烈主观判断，请勿阴阳或贬低任何一方。</span>
        </Alert>
        <div className="editor-actions"><Button disabled={!bodyDirty} onClick={() => { setWriters(namesText(baseWriters)); setBody(baseBody); }}>撤销修改</Button><Button variant="primary" loading={saving} disabled={!bodyDirty} onClick={() => void save()}><Save size={16} />保存正文</Button></div>
      </Panel>

      <Panel className="match-history-panel">
        <SectionTitle title="球队战绩与交锋" description="过往三届成绩、本届已完成比赛及两队近三届交锋。" />
        <div className="history-team-grid">
          {[match.home, match.away].map((team) => <article className="history-team-card" key={team.team_id}><h3>{teamName(team)}</h3><div className="history-section"><strong>过往三届战绩</strong><HistoryList values={team.previous_outcomes} empty="暂无" render={(value) => formatSeasonOutcome(value as SeasonOutcomeSnapshot)} /></div><div className="history-section"><strong>本届赛果</strong><HistoryList values={team.current_results} empty="暂无" render={(value) => formatPlayedMatch(value as PlayedMatchSnapshot)} /></div></article>)}
        </div>
        <div className="head-to-head-card"><strong>近三届交锋</strong><HistoryList values={match.head_to_head} empty="无" render={(value) => formatPlayedMatch(value as PlayedMatchSnapshot, true)} /></div>
      </Panel>

      <Panel className="ai-writing-panel">
        <SectionTitle title="AI 写作" description="根据当前比赛资料和人工描述生成一篇完整前瞻。" actions={<Sparkles size={20} />} />
        {aiContext ? <>
          <div className="ai-controls"><Field label="生成模型" htmlFor="ai-model"><select id="ai-model" value={selectedModel} onChange={(event) => { setSelectedModel(event.target.value); setCopied(false); }}>{aiContext.models.map((model) => <option key={model.profile} value={model.profile} disabled={!model.available}>{model.label} · 约 {model.estimated_seconds} 秒 · {model.score.toFixed(1)} 分{model.recommended ? " · 推荐" : ""}{model.available ? "" : " · 未配置"}</option>)}</select></Field><Button variant="primary" loading={startingAI} disabled={!selectedModelOption?.available || aiRunning} onClick={() => void startGeneration()}><Sparkles size={16} />{selectedResult?.status === "failed" || selectedResult?.is_stale ? "重新生成" : "生成前瞻"}</Button></div>
          {selectedModelOption ? <p className="ai-model-note">{selectedModelOption.label}：评测 {selectedModelOption.score.toFixed(1)} 分，通常约需 {selectedModelOption.estimated_seconds} 秒，实际耗时会随文章和服务负载变化。</p> : null}
          <Alert tone="warning">
            <strong>AI 内容必须人工复核</strong>
            <span>AI 仅提供写作初稿。保存或发布前，请逐项核对比赛、球队和球员事实，修正错误与不当表述，并由作者对最终文章负责。</span>
            <span>模型可能连续复用相同词语、句式或描述，造成表达单一；请检查高频词和重复表达，按需替换、精简或改写。</span>
          </Alert>
          {aiRunning ? <Alert tone="info">AI 正在后台生成。可以关闭或切换页面，稍后返回继续查看。</Alert> : null}
          {selectedResult?.error ? <Alert tone="danger">{selectedResult.error.message}</Alert> : null}
          {selectedResult?.content ? <div className="ai-result"><div className="ai-result__heading"><div><strong>生成正文</strong>{selectedResult.is_stale ? <Badge tone="warning">资料已变化，结果可能过期</Badge> : <Badge tone="success">当前资料</Badge>}</div><Button onClick={() => void copyResult()}><Copy size={16} />{copied ? "已复制" : "复制正文"}</Button></div><textarea readOnly aria-label="AI生成正文" rows={18} value={selectedResult.content} /></div> : null}
        </> : <LoadingScreen label="正在读取 AI 写作资料" />}
      </Panel>

      <Panel className="manual-description-panel">
        <SectionTitle title="人工描述" actions={<Button variant="primary" loading={savingManual} disabled={!manualDirty} onClick={() => void saveManual()}><Save size={16} />保存双方资料</Button>} />
        <Alert tone="info">
          <strong>及时维护可复用的人工资料</strong>
          <span>请填写球队与球员的风格、特点、位置、年级、伤病等人工信息，并在情况变化后及时更新。</span>
          <span>这些资料会随球队长期保存并用于组装 AI Prompt。建议不要重复填写系统可自动获取的战绩、进球、出场时间等数据。</span>
        </Alert>
        {manual ? <div className="manual-team-grid"><ManualTeamEditor side="主队" value={manual.home_team} onTeamChange={(value) => updateManualTeam("home_team", value)} onPlayerChange={(name, value) => updateManualPlayer("home_team", name, value)} /><ManualTeamEditor side="客队" value={manual.away_team} onTeamChange={(value) => updateManualTeam("away_team", value)} onPlayerChange={(name, value) => updateManualPlayer("away_team", name, value)} /></div> : <LoadingScreen label="正在读取人工资料" />}
      </Panel>

      {conflict ? <Modal title="正文已被其他请求更新" wide actions={<><Button onClick={loadServer}>加载服务器内容</Button><Button variant="primary" onClick={rebaseLocal}>保留本地内容并人工合并</Button></>}><Alert tone="warning">服务器保存序号已经变为 #{conflict.body_version}。系统不会自动覆盖，请比较后明确选择。</Alert><div className="conflict-grid"><div><strong>你的未保存内容</strong><span>署名：{writers || "—"}</span><pre>{body || "（空正文）"}</pre></div><div><strong>服务器当前内容</strong><span>署名：{namesText(conflict.writers) || "—"}</span><pre>{conflict.body || "（空正文）"}</pre></div></div></Modal> : null}
      {blocker.state === "blocked" ? <Modal title={manualDirty ? "有未保存的修改" : "有未保存的正文"} actions={<><Button onClick={() => blocker.reset()}>留在此页</Button><Button variant="danger" onClick={() => blocker.proceed()}>放弃修改并离开</Button></>}><p>{manualDirty ? "离开页面会丢失尚未保存的正文、署名或人工描述。" : "离开页面会丢失尚未保存的署名或正文。"}</p></Modal> : null}
    </>
  );
}
