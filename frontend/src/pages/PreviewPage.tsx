import { useCallback, useEffect, useState } from "react";
import { ArrowLeft, Maximize2, RefreshCw } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { api, errorMessage, getReportDiagnostics } from "../api";
import { useAuth } from "../auth";
import { Alert, Badge, Button, EmptyState, LoadingScreen, PageHeader, Panel, SectionTitle } from "../components";
import { ReportDiagnostics } from "../ReportDiagnostics";
import { competitionLabels, labelMissingField, type Article, type PreviewBatch, type PreviewMatch, type ReportRenderDiagnostic } from "../types";
import { formatDateTime } from "../utils";

export function PreviewPage({ articleType = "preview" }: { articleType?: "preview" | "report" }) {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const { batchId } = useParams();
  const [batch, setBatch] = useState<PreviewBatch | null>(null);
  const [article, setArticle] = useState<Article | null>(null);
  const [loading, setLoading] = useState(true);
  const [rendering, setRendering] = useState(false);
  const [reused, setReused] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [diagnostics, setDiagnostics] = useState<ReportRenderDiagnostic[]>([]);

  const load = useCallback(async (silent = false) => {
    if (!batchId) return;
    if (!silent) { setLoading(true); setError(null); }
    try {
      const batchValue = await api<PreviewBatch>(`/api/batches/${batchId}`);
      setBatch(batchValue);
      const articleId = articleType === "preview"
        ? batchValue.current_preview_article_id ?? batchValue.latest_preview_article_id
        : batchValue.current_report_article_id ?? batchValue.latest_report_article_id;
      setArticle(articleId ? await api<Article>(`/api/articles/${articleId}`) : null);
    } catch (value) { setError(errorMessage(value)); } finally { if (!silent) setLoading(false); }
  }, [articleType, batchId]);

  useEffect(() => { document.title = `${articleType === "preview" ? "前瞻" : "战报"}文章 · 绿茵宣传部`; void load(); }, [articleType, load]);
  useEffect(() => {
    if (articleType !== "preview") return;
    const refresh = () => { if (!document.hidden) void load(true); };
    const timer = window.setInterval(refresh, 5000);
    window.addEventListener("focus", refresh);
    return () => { window.clearInterval(timer); window.removeEventListener("focus", refresh); };
  }, [articleType, load]);

  const render = async () => {
    setRendering(true); setError(null); setDiagnostics([]);
    try {
      const value = await api<{ reused: boolean; article: Article; diagnostics?: ReportRenderDiagnostic[] }>(`/api/batches/${batchId}/render-${articleType}`, { method: "POST" });
      setArticle(value.article); setReused(value.reused); setDiagnostics(value.diagnostics ?? []); await load();
    } catch (value) { setError(errorMessage(value)); setDiagnostics(getReportDiagnostics(value)); } finally { setRendering(false); }
  };

  if (loading && !batch) return <LoadingScreen label="正在读取文章" />;
  const stale = articleType === "report" && Boolean(batch?.latest_report_article_id && !batch.current_report_article_id);
  const snapshotMatches = article && Array.isArray(article.input_snapshot.matches)
    ? article.input_snapshot.matches.filter((value): value is PreviewMatch => {
        if (!value || typeof value !== "object") return false;
        const item = value as Partial<PreviewMatch>;
        return typeof item.game_id === "number" && Boolean(item.home) && Boolean(item.away);
      })
    : [];
  return (
    <>
      <PageHeader eyebrow={articleType === "preview" ? "前瞻文章" : "战报文章"} title={batch ? `${competitionLabels[batch.competition]} · ${batch.batch_date}` : "文章预览"} description={articleType === "preview" ? "文章随批次内容自动更新，可直接查看最新排版。" : isAdmin ? "渲染结果保留为不可变记录，内容变化后需要重新渲染。" : "查看管理员最近一次渲染的文章记录。"} actions={<><Link className="button button--quiet" to={articleType === "preview" ? `/previews/${batchId}` : "/reports"}><ArrowLeft size={16} />返回{articleType === "preview" ? "批次" : "战报列表"}</Link>{isAdmin && articleType === "report" ? <Button variant="primary" loading={rendering} onClick={() => void render()}><RefreshCw size={16} />{article ? "重新渲染" : "渲染文章"}</Button> : null}</>} />
      {error ? <Alert tone="danger" onDismiss={() => setError(null)}>{error}</Alert> : null}
      {reused !== null ? <Alert tone={reused ? "info" : "success"}>{reused ? "内容没有变化，已复用当前渲染记录。" : "已生成新的渲染记录。"}</Alert> : null}
      {articleType === "report" ? <ReportDiagnostics diagnostics={diagnostics} matches={batch?.matches ?? []} /> : null}
      {stale ? <Alert tone="warning"><strong>当前数据已发生变化，文章已过期。</strong><span>{isAdmin ? "以下为最近一次渲染结果，请重新渲染后再用于发布。" : "以下仍是最近一次渲染结果，请等待管理员重新渲染。"}</span></Alert> : null}
      {!article ? <Panel><EmptyState title="尚未渲染文章" description={articleType === "preview" ? "文章会在批次创建和内容变化后自动生成。" : isAdmin ? "渲染时会实时查询批次中已完赛比赛，并生成战报文章。" : "请等待管理员完成文章渲染。"} action={isAdmin && articleType === "report" ? <Button variant="primary" loading={rendering} onClick={() => void render()}>开始渲染</Button> : undefined} /></Panel> : (
        <div className="preview-layout">
          <Panel className="preview-meta">
            <SectionTitle title="文章信息" />
            <dl className="detail-list">
              {articleType === "report" ? <div><dt>渲染记录</dt><dd>#{article.version_number}</dd></div> : null}
              <div><dt>状态</dt><dd><Badge tone={stale ? "warning" : article.is_complete ? "success" : "warning"}>{stale ? "已过期，仅供查看" : article.is_complete ? "完整，可发布" : "存在缺项"}</Badge></dd></div>
              <div><dt>标题</dt><dd>{article.title}</dd></div>
              <div><dt>作者</dt><dd>{article.author || "—"}</dd></div>
              <div><dt>{articleType === "preview" ? "更新时间" : "生成时间"}</dt><dd>{formatDateTime(article.created_at)}</dd></div>
              <div><dt>模板</dt><dd className="code-text">{article.template_version}</dd></div>
            </dl>
            {article.missing_fields.length ? <div className="missing-box"><strong>仍需补充</strong><div className="chip-list">{article.missing_fields.map((item) => <Badge tone="warning" key={item}>{labelMissingField(item, snapshotMatches)}</Badge>)}</div></div> : null}
          </Panel>
          <Panel className="article-frame-panel">
            <SectionTitle title="最终效果" description="以下内容由后端模板直接生成。" actions={<a className="button button--quiet" href={`/api/articles/${article.id}/preview?v=${encodeURIComponent(article.created_at)}`} target="_blank" rel="noreferrer"><Maximize2 size={15} />全屏预览</a>} />
            <iframe className="article-frame" title="文章预览" key={article.created_at} src={`/api/articles/${article.id}/preview?v=${encodeURIComponent(article.created_at)}`} />
          </Panel>
        </div>
      )}
    </>
  );
}
