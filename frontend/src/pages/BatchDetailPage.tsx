import { useCallback, useEffect, useState, type FormEvent } from "react";
import { ArrowLeft, ChevronRight, CloudSun, Copy, FileImage, FileText, Save, Send, Sparkles, Users } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { api, errorMessage, jsonBody } from "../api";
import { useAuth } from "../auth";
import { useClaimantNames } from "../claimants";
import { Alert, Badge, Button, EmptyState, Field, LoadingScreen, NameInput, PageHeader, Panel, SectionTitle } from "../components";
import { competitionLabels, labelMissingField, statusLabels, type AITitleContext, type AITitleResult, type PreviewBatch, type PreviewMatch, type Weather } from "../types";
import { formatDateTime, matchTaskStatus, namesText, parseNames, teamName } from "../utils";

function MatchCard({ batchId, match, claimantName }: { batchId: number; match: PreviewMatch; claimantName: string }) {
  const status = matchTaskStatus(match);
  const content = (
    <>
      <div className="match-card__heading">
        <span>{match.competition_name} · {match.stage}</span>
        <Badge tone={status.tone}>{status.label}</Badge>
      </div>
      <strong>{teamName(match.home)} <em>vs</em> {teamName(match.away)}</strong>
      <div className="match-card__meta">
        <span>{formatDateTime(match.kickoff)}</span>
        <span>{match.venue}</span>
        <span>认领人：<strong>{claimantName}</strong></span>
      </div>
      <ChevronRight className="match-card__arrow" size={18} aria-hidden />
    </>
  );
  return <Link className="match-card" to={`/previews/${batchId}/matches/${match.game_id}`}>{content}</Link>;
}

export function BatchDetailPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const { batchId } = useParams();
  const [batch, setBatch] = useState<PreviewBatch | null>(null);
  const [loading, setLoading] = useState(true);
  const [action, setAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [headline, setHeadline] = useState("");
  const [editors, setEditors] = useState("");
  const [reviewers, setReviewers] = useState("");
  const [approvers, setApprovers] = useState("");
  const [weather, setWeather] = useState({ condition: "", low_c: "", high_c: "", wind_direction: "", wind_level: "" });
  const [mediaId, setMediaId] = useState("");
  const [coverFile, setCoverFile] = useState<File | null>(null);
  const [titleContext, setTitleContext] = useState<AITitleContext | null>(null);
  const [titleModel, setTitleModel] = useState("");
  const [startingTitle, setStartingTitle] = useState(false);
  const [titlePromptCopied, setTitlePromptCopied] = useState(false);

  const refreshTitleContext = useCallback(async () => {
    if (!batchId) return;
    const value = await api<AITitleContext>(`/api/batches/${batchId}/ai-title-context`);
    setTitleContext(value);
    setTitleModel((current) => current || value.models.find((item) => item.available)?.profile || "");
  }, [batchId]);

  const applyBatch = useCallback((value: PreviewBatch) => {
    setBatch(value);
    setHeadline(value.headline);
    setEditors(namesText(value.editors));
    setReviewers(namesText(value.reviewers));
    setApprovers(namesText(value.approvers));
    setMediaId(value.cover.kind === "media_id" ? value.cover.storage_key : "");
    const current = value.weather;
    setWeather({
      condition: current?.condition ?? "",
      low_c: current ? String(current.low_c) : "",
      high_c: current ? String(current.high_c) : "",
      wind_direction: current?.wind_direction ?? "",
      wind_level: current?.wind_level ?? "",
    });
  }, []);

  const load = useCallback(async (quiet = false) => {
    if (!batchId) return;
    if (!quiet) setLoading(true);
    setError(null);
    try {
      const value = await api<PreviewBatch>(`/api/batches/${batchId}`); applyBatch(value);
      if (isAdmin) {
        const context = await api<AITitleContext>(`/api/batches/${batchId}/ai-title-context`);
        if (context && Array.isArray(context.models)) {
          setTitleContext(context);
          setTitleModel((current) => current || context.models.find((item) => item.available)?.profile || "");
        }
      }
    } catch (value) {
      setError(errorMessage(value));
    } finally {
      setLoading(false);
    }
  }, [applyBatch, batchId]);

  useEffect(() => { document.title = "批次详情 · 绿茵宣传部"; void load(); }, [load]);

  const selectedTitleResult = titleContext?.results[titleModel];
  const titleRunning = selectedTitleResult?.status === "queued" || selectedTitleResult?.status === "running";
  useEffect(() => {
    if (!batchId || !titleModel || !selectedTitleResult || !["queued", "running"].includes(selectedTitleResult.status)) return;
    let active = true;
    const poll = async () => {
      try {
        const value = await api<AITitleResult>(`/api/batches/${batchId}/ai-title-results/${encodeURIComponent(titleModel)}`);
        if (!active) return;
        setTitleContext((current) => current ? { ...current, results: { ...current.results, [titleModel]: value } } : current);
        if (!["queued", "running"].includes(value.status)) await refreshTitleContext();
      } catch (value) {
        if (active) setError(errorMessage(value));
      }
    };
    const timer = window.setInterval(() => { void poll(); }, 2500);
    return () => { active = false; window.clearInterval(timer); };
  }, [batchId, refreshTitleContext, selectedTitleResult, titleModel]);

  const run = async (name: string, task: () => Promise<unknown>, message: string) => {
    setAction(name); setError(null); setSuccess(null);
    try {
      await task(); setSuccess(message); await load(true);
    } catch (value) {
      setError(errorMessage(value));
    } finally {
      setAction(null);
    }
  };
  const saveHeadline = (event: FormEvent) => {
    event.preventDefault();
    void run("headline", () => api(`/api/batches/${batchId}`, { method: "PATCH", ...jsonBody({ headline }) }), "文章标题已保存");
  };
  const generateTitle = async () => {
    if (!batchId || !titleModel) return;
    setStartingTitle(true); setError(null); setSuccess(null);
    try {
      const result = await api<AITitleResult>(`/api/batches/${batchId}/ai-title-generations`, { method: "POST", ...jsonBody({ model_profile: titleModel }) });
      setTitleContext((current) => current ? { ...current, results: { ...current.results, [titleModel]: result } } : current);
      if (result.status === "succeeded") setSuccess(result.reused ? "已载入此前生成的标题候选。" : "AI 标题已生成。");
    } catch (value) { setError(errorMessage(value)); } finally { setStartingTitle(false); }
  };
  const copyTitlePrompt = async () => {
    if (!batchId) return;
    try { const value = await api<{ copy_text: string }>(`/api/batches/${batchId}/ai-title-prompt`); await navigator.clipboard.writeText(value.copy_text); setTitlePromptCopied(true); window.setTimeout(() => setTitlePromptCopied(false), 1800); } catch { setError("无法复制标题 Prompt，请稍后重试。"); }
  };
  const savePersonnel = (event: FormEvent) => {
    event.preventDefault();
    void run("personnel", () => api(`/api/batches/${batchId}`, { method: "PATCH", ...jsonBody({ editors: parseNames(editors), reviewers: parseNames(reviewers), approvers: parseNames(approvers) }) }), "人员设置已保存");
  };
  const saveWeather = (event: FormEvent) => {
    event.preventDefault();
    void run("weather", () => api<Weather>(`/api/weather/${batch?.batch_date}`, { method: "PUT", ...jsonBody({ condition: weather.condition, low_c: Number(weather.low_c), high_c: Number(weather.high_c), wind_direction: weather.wind_direction, wind_level: weather.wind_level }) }), "天气已保存为人工数据");
  };
  const uploadCover = (event: FormEvent) => {
    event.preventDefault();
    if (!coverFile) return;
    const data = new FormData(); data.append("file", coverFile);
    void run("cover-file", () => api(`/api/batches/${batchId}/cover`, { method: "POST", body: data }), "封面文件已更新");
  };
  const saveMediaId = (event: FormEvent) => {
    event.preventDefault();
    void run("cover-media", () => api(`/api/batches/${batchId}/cover-media-id`, { method: "PUT", ...jsonBody({ media_id: mediaId }) }), "永久素材封面已更新");
  };

  const claimantNames = useClaimantNames((batch?.matches ?? []).map((match) => match.claimed_by_user_id));

  if (loading && !batch) return <LoadingScreen label="正在读取批次详情" />;
  if (!batch) return <><PageHeader title="批次详情" /><Alert tone="danger">{error || "批次不存在"}</Alert></>;
  const matches = batch.matches ?? [];
  const activeMatches = matches.filter((match) => match.active);
  const openCount = activeMatches.filter((match) => match.task_open).length;
  return (
    <>
      <PageHeader eyebrow={`批次 #${batch.id}`} title={`${batch.batch_date} · ${competitionLabels[batch.competition]}`} description={batch.headline || "尚未填写文章标题"} actions={<><Link className="button button--quiet" to="/previews"><ArrowLeft size={16} />返回列表</Link><Link className="button button--primary" to={`/previews/${batch.id}/article`}><FileText size={16} />文章预览</Link></>} />
      {error ? <Alert tone="danger" onDismiss={() => setError(null)}>{error}</Alert> : null}
      {success ? <Alert tone="success" onDismiss={() => setSuccess(null)}>{success}</Alert> : null}
      <div className="batch-summary">
        <div><span>当前状态</span><Badge tone={batch.preview_status === "ready" ? "success" : batch.preview_status === "drafted" ? "info" : "warning"}>{statusLabels[batch.preview_status]}</Badge></div>
        <div><span>有效比赛</span><strong>{activeMatches.length}</strong></div>
        <div><span>开放任务</span><strong>{openCount}/{activeMatches.length}</strong></div>
      </div>
      {batch.last_error ? <Alert tone="warning"><strong>{batch.last_error.code}</strong>{batch.last_error.message}<span>{formatDateTime(batch.last_error.at)}</span></Alert> : null}
      {batch.missing_fields.length ? <Panel className="missing-panel"><SectionTitle title="完整性缺项" description="这些状态由后端实时计算。" /><div className="chip-list">{batch.missing_fields.map((item) => <Badge tone="warning" key={item}>{labelMissingField(item, batch.matches ?? [])}</Badge>)}</div></Panel> : <Alert tone="success">{isAdmin ? "当前内容完整，可以渲染并加入微信草稿。" : "当前批次内容完整。"}</Alert>}

      {isAdmin ? <Panel className="headline-panel">
        <SectionTitle title="文章标题" description="标题是文章的主要信息，修改后需要重新渲染文章。" actions={<FileText size={20} />} />
        <form className="headline-form" onSubmit={saveHeadline}>
          <Field label="标题内容" htmlFor="batch-headline"><input id="batch-headline" className="headline-input" value={headline} maxLength={200} placeholder="填写本批次的文章标题" onChange={(event) => setHeadline(event.target.value)} /></Field>
          <Button variant="primary" loading={action === "headline"} type="submit"><Save size={16} />保存标题</Button>
        </form>
      </Panel> : null}
      {isAdmin ? <Panel className="headline-panel">
        <SectionTitle title="AI 标题" description={titleContext?.prefix || "根据当前批次前瞻生成六条候选标题。"} actions={<Sparkles size={20} />} />
        {titleContext ? <><div className="ai-controls"><Field label="生成模型"><select value={titleModel} onChange={(event) => setTitleModel(event.target.value)}>{titleContext.models.map((model) => <option key={model.profile} value={model.profile} disabled={!model.available}>{model.label}{model.available ? "" : " · 未配置"}</option>)}</select></Field><div className="button-row"><Button variant="primary" loading={startingTitle} disabled={!titleContext.models.find((model) => model.profile === titleModel)?.available || titleRunning} onClick={() => void generateTitle()}><Sparkles size={16} />{selectedTitleResult?.status === "failed" || selectedTitleResult?.is_stale ? "重新生成" : "生成标题候选"}</Button><Button onClick={() => void copyTitlePrompt()}><Copy size={16} />{titlePromptCopied ? "Prompt 已复制" : "复制 Prompt"}</Button></div></div>{titleRunning ? <Alert tone="info">AI 正在后台生成。可以关闭或切换页面，稍后返回继续查看。</Alert> : null}{selectedTitleResult?.error ? <Alert tone="danger">{selectedTitleResult.error.message}</Alert> : null}<div className="title-candidate-list">{selectedTitleResult?.candidates?.map((candidate) => { const title = `${titleContext.prefix}|| ${candidate.idiom_1}，${candidate.idiom_2}`; return <div className="title-candidate" key={title}><span>{title}</span><Button onClick={() => setHeadline(title)}>使用此标题</Button></div>; })}</div></> : <LoadingScreen label="正在读取标题 AI" />}
      </Panel> : null}

      <Panel className="matches-overview-panel">
        <SectionTitle title="比赛" description={`${matches.length} 场比赛，当前开放 ${openCount}/${activeMatches.length} 场有效比赛。`} actions={<Send size={20} />} />
        {!matches.length ? <EmptyState title="当前没有比赛" description={isAdmin ? "可以返回批次列表重新查询数据。" : "该批次暂时没有比赛。"} /> : <div className="match-card-list">{matches.map((match) => <MatchCard key={match.game_id} batchId={batch.id} match={match} claimantName={match.claimed_by_user_id === null ? "未认领" : match.claimed_by_user_id === user?.id ? user.display_name : claimantNames[match.claimed_by_user_id] ?? "读取中…"} />)}</div>}
        {isAdmin ? <><p className="panel-copy">开放或关闭操作会一次作用于当前批次的全部有效比赛，不影响已填写的正文。</p><div className="button-row"><Button variant="primary" loading={action === "open"} onClick={() => void run("open", () => api(`/api/batches/${batch.id}/open-tasks`, { method: "POST" }), "已开放全部有效比赛")}>开放全部任务</Button><Button loading={action === "close"} onClick={() => void run("close", () => api(`/api/batches/${batch.id}/close-tasks`, { method: "POST" }), "已关闭全部有效比赛")}>关闭全部任务</Button></div></> : <p className="panel-copy">所有比赛均可查看，只有本人已认领的比赛可以编辑。</p>}
      </Panel>

      {isAdmin ? <section className="admin-settings" aria-labelledby="admin-settings-title">
        <div className="admin-settings__heading">
          <p className="eyebrow">ADMIN SETTINGS</p>
          <h2 id="admin-settings-title">管理设置</h2>
          <p>人员、天气与封面等低频配置，仅供管理员维护。</p>
        </div>
        <div className="admin-settings__grid">
          <Panel>
            <SectionTitle title="批次信息" description="编辑、责编和审核人员设置。修改后会使当前渲染记录过期。" actions={<Users size={20} />} />
            <form className="stack" onSubmit={savePersonnel}>
              <Field label="编辑"><NameInput value={editors} onChange={setEditors} /></Field>
              <Field label="责编"><NameInput value={reviewers} onChange={setReviewers} /></Field>
              <Field label="审核"><NameInput value={approvers} onChange={setApprovers} /></Field>
              <Button variant="primary" loading={action === "personnel"} type="submit"><Save size={16} />保存人员设置</Button>
            </form>
          </Panel>

          <Panel>
            <SectionTitle title="天气" description={batch.weather ? `${batch.weather.region_name} · ${batch.weather.source === "manual" ? "人工" : "自动"}数据` : "尚无天气数据"} actions={<CloudSun size={20} />} />
            <form className="stack" onSubmit={saveWeather}>
              <div className="form-grid"><Field label="天气"><input value={weather.condition} required onChange={(event) => setWeather({ ...weather, condition: event.target.value })} /></Field><Field label="风向"><input value={weather.wind_direction} required onChange={(event) => setWeather({ ...weather, wind_direction: event.target.value })} /></Field><Field label="最低温"><input type="number" min={-50} max={60} value={weather.low_c} required onChange={(event) => setWeather({ ...weather, low_c: event.target.value })} /></Field><Field label="最高温"><input type="number" min={-50} max={60} value={weather.high_c} required onChange={(event) => setWeather({ ...weather, high_c: event.target.value })} /></Field></div>
              <Field label="风力"><input value={weather.wind_level} required onChange={(event) => setWeather({ ...weather, wind_level: event.target.value })} /></Field>
              <Button variant="primary" loading={action === "weather"} type="submit"><Save size={16} />保存天气</Button>
            </form>
          </Panel>

          <Panel>
            <SectionTitle title="封面" description={`当前：${batch.cover.kind === "file" ? "上传文件" : "微信永久素材"}`} actions={<FileImage size={20} />} />
            <form className="stack" onSubmit={uploadCover}>
              <Field label="上传图片" hint="JPEG、PNG 或 GIF，最大 10 MiB。"><input type="file" accept="image/jpeg,image/png,image/gif" onChange={(event) => setCoverFile(event.target.files?.[0] ?? null)} required /></Field>
              <Button loading={action === "cover-file"} type="submit" disabled={!coverFile}>上传并替换</Button>
            </form>
            <div className="divider"><span>或者</span></div>
            <form className="stack" onSubmit={saveMediaId}>
              <Field label="永久素材 Media ID"><input value={mediaId} onChange={(event) => setMediaId(event.target.value)} required /></Field>
              <Button loading={action === "cover-media"} type="submit">使用永久素材</Button>
            </form>
          </Panel>
        </div>
      </section> : null}
    </>
  );
}
