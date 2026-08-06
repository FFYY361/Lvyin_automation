import { useCallback, useEffect, useState } from "react";
import { ArrowRight, RefreshCw, Search } from "lucide-react";
import { Link } from "react-router-dom";
import { api, errorMessage } from "../api";
import { useAuth } from "../auth";
import { Alert, Badge, Button, EmptyState, LoadingScreen, PageHeader, Panel } from "../components";
import { competitionLabels, labelMissingField, statusLabels, type BatchStatus, type Competition, type PreviewBatch } from "../types";
import { formatDate, formatDateTime } from "../utils";

function statusTone(status: BatchStatus): string {
  return status === "ready" ? "success" : status === "drafted" ? "info" : "warning";
}

export function BatchesPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [items, setItems] = useState<PreviewBatch[]>([]);
  const [date, setDate] = useState("");
  const [competition, setCompetition] = useState<Competition | "">("");
  const [status, setStatus] = useState<BatchStatus | "">("");
  const [showFutsal, setShowFutsal] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const query = new URLSearchParams();
    if (date) query.set("batch_date", date);
    if (competition) query.set("competition", competition);
    if (status) query.set("preview_status", status);
    try {
      const result = await api<{ items: PreviewBatch[] }>(`/api/batches${query.size ? `?${query}` : ""}`);
      setItems(result.items);
    } catch (value) {
      setError(errorMessage(value));
    } finally {
      setLoading(false);
    }
  }, [competition, date, status]);

  useEffect(() => { document.title = "批次管理 · 前瞻管理"; void load(); }, [load]);

  const refresh = async (id: number) => {
    setRefreshing(id);
    setError(null);
    try {
      await api(`/api/batches/${id}/refresh-data`, { method: "POST" });
      await load();
    } catch (value) {
      setError(errorMessage(value));
    } finally {
      setRefreshing(null);
    }
  };

  const visibleItems = showFutsal ? items : items.filter((batch) => batch.competition !== "futsal");

  return (
    <>
      <PageHeader eyebrow="内容管理" title="前瞻批次" description={isAdmin ? "查看状态、定位缺项并重新查询比赛数据。" : "查看批次、比赛摘要和文章预览。"} />
      <Panel className="filter-bar batch-filter">
        <label><span>日期</span><input type="date" value={date} onChange={(event) => setDate(event.target.value)} /></label>
        <label><span>赛事</span><select value={competition} onChange={(event) => setCompetition(event.target.value as Competition | "")}><option value="">全部赛事</option><option value="male">男足</option><option value="female">女足</option><option value="futsal">五人制</option></select></label>
        <label><span>状态</span><select value={status} onChange={(event) => setStatus(event.target.value as BatchStatus | "")}><option value="">全部状态</option><option value="incomplete">待完善</option><option value="ready">可发布</option><option value="drafted">已建草稿</option></select></label>
        <label className="filter-checkbox"><input type="checkbox" checked={showFutsal} onChange={(event) => setShowFutsal(event.target.checked)} /><span>显示五人制批次</span></label>
        <Button onClick={() => void load()}><Search size={16} />查询</Button>
      </Panel>
      {error ? <Alert tone="danger" onDismiss={() => setError(null)}>{error}</Alert> : null}
      {loading ? <LoadingScreen label="正在读取批次" /> : !visibleItems.length ? <Panel><EmptyState title="没有匹配的批次" description="调整筛选条件，或先创建新的前瞻批次。" /></Panel> : (
        <Panel className="table-panel">
          <div className="data-table-wrap">
            <table className="data-table">
              <thead><tr><th>日期</th><th>赛事</th><th>状态</th><th>缺项</th><th>更新时间</th><th><span className="sr-only">操作</span></th></tr></thead>
              <tbody>{visibleItems.map((batch) => (
                <tr key={batch.id}>
                  <td data-label="日期"><strong>{formatDate(batch.batch_date)}</strong><small>#{batch.id}</small></td>
                  <td data-label="赛事">{competitionLabels[batch.competition]}</td>
                  <td data-label="状态"><Badge tone={statusTone(batch.preview_status)}>{statusLabels[batch.preview_status]}</Badge></td>
                  <td data-label="缺项">{batch.missing_fields.length ? <span title={batch.missing_fields.map((item) => labelMissingField(item)).join("、")}>{batch.missing_fields.length} 项待补充</span> : <span className="success-text">完整</span>}</td>
                  <td data-label="更新时间">{formatDateTime(batch.updated_at)}</td>
                  <td data-label="操作"><div className="row-actions">{isAdmin ? <Button variant="quiet" loading={refreshing === batch.id} onClick={() => void refresh(batch.id)} title="重新查询"><RefreshCw size={16} /></Button> : null}<Link className="icon-link" to={`/previews/${batch.id}`} aria-label="打开批次"><ArrowRight size={17} /></Link></div></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        </Panel>
      )}
    </>
  );
}
