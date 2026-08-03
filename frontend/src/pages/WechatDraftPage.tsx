import { useEffect, useState } from "react";
import { ArrowDown, ArrowUp, Check, GripVertical } from "lucide-react";
import { api, errorMessage, jsonBody } from "../api";
import { Alert, Badge, Button, EmptyState, LoadingScreen, Modal, PageHeader, Panel, SectionTitle } from "../components";
import { competitionLabels, type Article, type DraftResponse, type PreviewBatch, type WechatDraft } from "../types";
import { formatDate, formatDateTime, moveItem } from "../utils";

interface Candidate { batch: PreviewBatch; article: Article }

export function WechatDraftPage() {
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [selected, setSelected] = useState<number[]>([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<Extract<DraftResponse, { status: "ready" }> | null>(null);
  const [result, setResult] = useState<{ status: "created" | "reused"; draft: WechatDraft } | null>(null);
  const [dragged, setDragged] = useState<number | null>(null);

  useEffect(() => {
    document.title = "微信草稿 · 前瞻管理";
    api<{ items: PreviewBatch[] }>("/api/preview-batches").then(async ({ items }) => {
      const withArticles = items.filter((item) => item.current_article_id).map(async (batch) => ({ batch, article: await api<Article>(`/api/articles/${batch.current_article_id}`) }));
      const values = await Promise.all(withArticles);
      setCandidates(values.filter(({ article }) => article.is_complete && article.is_current !== false));
    }).catch((value) => setError(errorMessage(value))).finally(() => setLoading(false));
  }, []);

  const toggle = (id: number) => {
    setPreview(null); setResult(null);
    setSelected((current) => current.includes(id) ? current.filter((item) => item !== id) : current.length >= 8 ? current : [...current, id]);
  };
  const move = (from: number, to: number) => {
    if (to < 0 || to >= selected.length) return;
    setSelected((current) => moveItem(current, from, to));
    setPreview(null);
  };
  const reorderById = (sourceId: number, targetId: number) => {
    const from = selected.indexOf(sourceId); const to = selected.indexOf(targetId);
    if (from >= 0 && to >= 0) move(from, to);
  };
  const validate = async () => {
    setSubmitting(true); setError(null); setResult(null);
    try {
      const value = await api<DraftResponse>("/api/wechat-drafts", { method: "POST", ...jsonBody({ article_ids: selected, confirm: false }) });
      if (value.status === "ready") setPreview(value); else setResult(value);
    } catch (value) { setError(errorMessage(value)); } finally { setSubmitting(false); }
  };
  const confirm = async () => {
    setSubmitting(true); setError(null);
    try {
      const value = await api<DraftResponse>("/api/wechat-drafts", { method: "POST", ...jsonBody({ article_ids: selected, confirm: true }) });
      if (value.status !== "ready") setResult(value);
      setPreview(null);
    } catch (value) { setError(errorMessage(value)); setPreview(null); } finally { setSubmitting(false); }
  };

  const ordered = selected.map((id) => candidates.find(({ article }) => article.id === id)).filter(Boolean) as Candidate[];
  if (loading) return <LoadingScreen label="正在汇集可发布文章" />;
  return (
    <>
      <PageHeader eyebrow="发布" title="创建微信草稿" description="选择当前完整文章，顺序即公众号头条与次条顺序。" actions={<Button variant="primary" loading={submitting} disabled={!selected.length} onClick={() => void validate()}>核对并继续</Button>} />
      {error ? <Alert tone="danger" onDismiss={() => setError(null)}>{error}</Alert> : null}
      {result ? <Alert tone="success"><strong>{result.status === "reused" ? "已复用已有草稿" : "微信草稿创建成功"}</strong><span className="result-media">media_id：<code>{result.draft.media_id}</code></span><span>微信回执时间：{formatDateTime(result.draft.wechat_created_at)}</span></Alert> : null}
      <div className="draft-layout">
        <Panel>
          <SectionTitle title="可选文章" description="仅显示每个批次当前且完整的文章。" />
          {!candidates.length ? <EmptyState title="暂时没有完整文章" description="请先完善批次并渲染文章。" /> : <div className="candidate-list">{candidates.map(({ batch, article }) => {
            const checked = selected.includes(article.id);
            return <label className="candidate-card" key={article.id}><input type="checkbox" checked={checked} disabled={!checked && selected.length >= 8} onChange={() => toggle(article.id)} /><div><strong>{article.title}</strong><span>{formatDate(batch.preview_date)} · {competitionLabels[batch.competition]} · v{article.version_number}</span></div>{checked ? <Check size={18} /> : null}</label>;
          })}</div>}
        </Panel>
        <Panel>
          <SectionTitle title={`发布顺序（${selected.length}/8）`} description="第一篇为头条。拖动或使用按钮调整。" />
          {!ordered.length ? <EmptyState title="尚未选择文章" description="从左侧选择 1–8 篇文章。" /> : <ol className="order-list">{ordered.map(({ article, batch }, index) => (
            <li key={article.id} draggable onDragStart={() => setDragged(article.id)} onDragOver={(event) => event.preventDefault()} onDrop={() => { if (dragged) reorderById(dragged, article.id); setDragged(null); }}>
              <GripVertical size={17} className="drag-handle" /><span className="order-number">{index + 1}</span><div><strong>{article.title}</strong><span>{competitionLabels[batch.competition]} · {index === 0 ? "头条" : `次条 ${index}`}</span></div><div className="order-actions"><button onClick={() => move(index, index - 1)} disabled={index === 0} aria-label="上移"><ArrowUp size={15} /></button><button onClick={() => move(index, index + 1)} disabled={index === ordered.length - 1} aria-label="下移"><ArrowDown size={15} /></button></div>
            </li>
          ))}</ol>}
        </Panel>
      </div>
      {preview ? <Modal title="确认创建微信草稿" wide actions={<><Button onClick={() => setPreview(null)}>返回调整</Button><Button variant="primary" loading={submitting} onClick={() => void confirm()}>确认真实创建</Button></>} onClose={() => setPreview(null)}>
        <Alert tone="warning">下一步会调用微信公众号接口并产生真实草稿。请最后核对文章与顺序。</Alert>
        <ol className="confirm-list">{ordered.map(({ article }, index) => <li key={article.id}><Badge tone={index === 0 ? "info" : "neutral"}>{index === 0 ? "头条" : `次条 ${index}`}</Badge><strong>{article.title}</strong></li>)}</ol>
        <div className="fingerprint"><span>发布指纹</span><code>{preview.publication_fingerprint}</code></div>
      </Modal> : null}
    </>
  );
}
