import { useCallback, useEffect, useState } from "react";
import { ArrowLeft, RefreshCw } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { api, errorMessage } from "../api";
import { useAuth } from "../auth";
import { Alert, Badge, Button, EmptyState, LoadingScreen, PageHeader, Panel, SectionTitle } from "../components";
import { competitionLabels, labelMissingField, type Article, type PreviewBatch, type PreviewMatch } from "../types";
import { formatDateTime } from "../utils";

export function PreviewPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const { batchId } = useParams();
  const [batch, setBatch] = useState<PreviewBatch | null>(null);
  const [article, setArticle] = useState<Article | null>(null);
  const [loading, setLoading] = useState(true);
  const [rendering, setRendering] = useState(false);
  const [reused, setReused] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!batchId) return;
    setLoading(true); setError(null);
    try {
      const batchValue = await api<PreviewBatch>(`/api/preview-batches/${batchId}`);
      setBatch(batchValue);
      const articleId = batchValue.current_article_id ?? batchValue.latest_article_id;
      setArticle(articleId ? await api<Article>(`/api/articles/${articleId}`) : null);
    } catch (value) { setError(errorMessage(value)); } finally { setLoading(false); }
  }, [batchId]);

  useEffect(() => { document.title = "文章预览 · 前瞻管理"; void load(); }, [load]);

  const render = async () => {
    setRendering(true); setError(null);
    try {
      const value = await api<{ reused: boolean; article: Article }>(`/api/preview-batches/${batchId}/render`, { method: "POST" });
      setArticle(value.article); setReused(value.reused); await load();
    } catch (value) { setError(errorMessage(value)); } finally { setRendering(false); }
  };

  if (loading && !batch) return <LoadingScreen label="正在读取文章" />;
  const stale = Boolean(batch?.latest_article_id && !batch.current_article_id);
  const snapshotMatches = article && Array.isArray(article.input_snapshot.matches)
    ? article.input_snapshot.matches.filter((value): value is PreviewMatch => {
        if (!value || typeof value !== "object") return false;
        const item = value as Partial<PreviewMatch>;
        return typeof item.game_id === "number" && Boolean(item.home) && Boolean(item.away);
      })
    : [];
  return (
    <>
      <PageHeader eyebrow="文章" title={batch ? `${competitionLabels[batch.competition]} · ${batch.preview_date}` : "文章预览"} description={isAdmin ? "渲染结果保留为不可变版本，内容变化后需要重新渲染。" : "查看管理员最近一次渲染的文章版本。"} actions={<><Link className="button button--quiet" to={`/batches/${batchId}`}><ArrowLeft size={16} />返回批次</Link>{isAdmin ? <Button variant="primary" loading={rendering} onClick={() => void render()}><RefreshCw size={16} />{article ? "重新渲染" : "渲染文章"}</Button> : null}</>} />
      {error ? <Alert tone="danger" onDismiss={() => setError(null)}>{error}</Alert> : null}
      {reused !== null ? <Alert tone={reused ? "info" : "success"}>{reused ? "内容没有变化，已复用当前文章版本。" : "已生成新的文章版本。"}</Alert> : null}
      {stale ? <Alert tone="warning"><strong>当前数据已发生变化，文章已过期。</strong><span>{isAdmin ? "以下为最近一次渲染结果，请重新渲染后再用于发布。" : "以下仍是最近一次渲染结果，请等待管理员重新渲染。"}</span></Alert> : null}
      {!article ? <Panel><EmptyState title="尚未渲染文章" description={isAdmin ? "可以先渲染带缺项提示的预览，完善内容后再重新渲染。" : "请等待管理员完成文章渲染。"} action={isAdmin ? <Button variant="primary" loading={rendering} onClick={() => void render()}>开始渲染</Button> : undefined} /></Panel> : (
        <div className="preview-layout">
          <Panel className="preview-meta">
            <SectionTitle title="文章信息" />
            <dl className="detail-list">
              <div><dt>版本</dt><dd>v{article.version_number}</dd></div>
              <div><dt>状态</dt><dd><Badge tone={stale ? "warning" : article.is_complete ? "success" : "warning"}>{stale ? "已过期，仅供查看" : article.is_complete ? "完整，可发布" : "存在缺项"}</Badge></dd></div>
              <div><dt>标题</dt><dd>{article.title}</dd></div>
              <div><dt>作者</dt><dd>{article.author || "—"}</dd></div>
              <div><dt>生成时间</dt><dd>{formatDateTime(article.created_at)}</dd></div>
              <div><dt>模板</dt><dd className="code-text">{article.template_version}</dd></div>
            </dl>
            {article.missing_fields.length ? <div className="missing-box"><strong>仍需补充</strong><div className="chip-list">{article.missing_fields.map((item) => <Badge tone="warning" key={item}>{labelMissingField(item, snapshotMatches)}</Badge>)}</div></div> : null}
          </Panel>
          <Panel className="article-frame-panel">
            <SectionTitle title="最终效果" description="以下内容由后端模板直接生成。" />
            <iframe className="article-frame" title="文章预览" src={`/api/articles/${article.id}/preview`} />
          </Panel>
        </div>
      )}
    </>
  );
}
