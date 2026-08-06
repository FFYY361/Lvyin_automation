import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { ArrowRight, ClipboardCheck, Hand, Search, UserRoundCog } from "lucide-react";
import { Link } from "react-router-dom";
import { ApiError, api, errorMessage, jsonBody } from "../api";
import { useAuth } from "../auth";
import { useClaimantNames } from "../claimants";
import { Alert, Badge, Button, EmptyState, LoadingScreen, Modal, PageHeader, Panel, SectionTitle } from "../components";
import { competitionLabels, type AdminUser, type Competition, type TaskMatch } from "../types";
import { formatDateTime, matchTaskStatus, teamName } from "../utils";

function isCurrentlyOpen(match: TaskMatch) {
  return match.active && match.task_open;
}

function byKickoffDescending(left: TaskMatch, right: TaskMatch) {
  const timeDifference = Date.parse(right.kickoff) - Date.parse(left.kickoff);
  return timeDifference || right.game_id - left.game_id;
}

function TaskCard({
  match,
  claimantName,
  canEnter,
  claiming,
  onClaim,
  onRelease,
  onAssign,
}: {
  match: TaskMatch;
  claimantName: string;
  canEnter: boolean;
  claiming: boolean;
  onClaim?: () => void;
  onRelease?: () => void;
  onAssign?: () => void;
}) {
  const status = matchTaskStatus(match);
  return (
    <article className="task-card">
      <div className="task-card__top">
        <span>{competitionLabels[match.competition]} · {match.competition_name} · {match.stage}</span>
        <Badge tone={status.tone}>{status.label}</Badge>
      </div>
      <h3>{teamName(match.home)} <em>vs</em> {teamName(match.away)}</h3>
      <div className="task-card__meta">
        <span>{formatDateTime(match.kickoff)}</span>
        <span>{match.venue}</span>
        <span>认领人：<strong>{claimantName}</strong></span>
      </div>
      <div className="task-card__actions">
        {onClaim ? <Button variant="primary" loading={claiming} onClick={onClaim}><Hand size={15} />领取任务</Button> : null}
        {onAssign ? <Button onClick={onAssign}><UserRoundCog size={15} />转交</Button> : null}
        {onRelease ? <Button variant="quiet" onClick={onRelease}>释放</Button> : null}
        {canEnter ? <Link className="button button--quiet" to={`/previews/${match.batch_id}/matches/${match.game_id}`}>进入比赛<ArrowRight size={15} /></Link> : null}
      </div>
    </article>
  );
}

function TaskSection({
  title,
  description,
  items,
  render,
  actions,
}: {
  title: string;
  description: string;
  items: TaskMatch[];
  render: (match: TaskMatch) => ReactNode;
  actions?: ReactNode;
}) {
  return (
    <Panel className="task-section">
      <SectionTitle title={`${title}（${items.length}）`} description={description} actions={actions} />
      {!items.length ? <EmptyState title={`暂无${title}`} /> : <div className="task-grid">{items.map(render)}</div>}
    </Panel>
  );
}

export function TasksPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [myTasks, setMyTasks] = useState<TaskMatch[]>([]);
  const [waitingTasks, setWaitingTasks] = useState<TaskMatch[]>([]);
  const [openTasks, setOpenTasks] = useState<TaskMatch[]>([]);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [date, setDate] = useState("");
  const [competition, setCompetition] = useState<Competition | "">("");
  const [showUnavailableTasks, setShowUnavailableTasks] = useState(false);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [releaseTarget, setReleaseTarget] = useState<TaskMatch | null>(null);
  const [assignTarget, setAssignTarget] = useState<TaskMatch | null>(null);
  const [assignedUserId, setAssignedUserId] = useState("");

  const load = useCallback(async (quiet = false) => {
    if (!quiet) { setLoading(true); setError(null); }
    try {
      const [mine, waiting, open, adminUsers] = await Promise.all([
        api<{ items: TaskMatch[] }>("/api/me/tasks"),
        api<{ items: TaskMatch[] }>("/api/tasks/wait_claim"),
        isAdmin ? api<{ items: TaskMatch[] }>("/api/tasks/open") : Promise.resolve({ items: [] }),
        isAdmin ? api<{ items: AdminUser[] }>("/api/admin/users") : Promise.resolve({ items: [] }),
      ]);
      setMyTasks(mine.items); setWaitingTasks(waiting.items); setOpenTasks(open.items); setUsers(adminUsers.items);
    } catch (value) {
      setError(errorMessage(value));
    } finally {
      setLoading(false);
    }
  }, [isAdmin]);

  useEffect(() => { document.title = "任务中心 · 前瞻协作"; void load(); }, [load]);

  const allTasks = [...myTasks, ...waitingTasks, ...openTasks];
  const claimantNames = useClaimantNames(allTasks.map((match) => match.claimed_by_user_id));
  const filter = useCallback((match: TaskMatch) => (
    (!date || match.kickoff.slice(0, 10) === date) && (!competition || match.competition === competition)
  ), [competition, date]);
  const filteredMine = useMemo(() => myTasks
    .filter(filter)
    .filter((match) => showUnavailableTasks || isCurrentlyOpen(match))
    .sort((left, right) => (
      Number(isCurrentlyOpen(right)) - Number(isCurrentlyOpen(left))
      || byKickoffDescending(left, right)
    )), [filter, myTasks, showUnavailableTasks]);
  const filteredWaiting = useMemo(() => waitingTasks.filter(filter).sort(byKickoffDescending), [filter, waitingTasks]);
  const filteredOpen = useMemo(() => openTasks.filter(filter).sort(byKickoffDescending), [filter, openTasks]);
  const claimant = (match: TaskMatch) => match.claimed_by_user_id === null
    ? "未认领"
    : match.claimed_by_user_id === user?.id ? user.display_name : claimantNames[match.claimed_by_user_id] ?? "读取中…";

  const claim = async (match: TaskMatch) => {
    setWorking(match.game_id); setError(null); setSuccess(null);
    try {
      await api(`/api/matches/${match.game_id}/claim`, { method: "POST" });
      setSuccess(`已领取 ${teamName(match.home)} vs ${teamName(match.away)}`);
      await load(true);
    } catch (value) {
      if (value instanceof ApiError && value.code === "task_claimed") setError("该任务刚刚被其他成员领取，列表已刷新。");
      else if (value instanceof ApiError && value.code === "task_unavailable") setError("该任务已经关闭或失效，无法领取。");
      else setError(errorMessage(value));
      await load(true);
    } finally { setWorking(null); }
  };
  const release = async () => {
    if (!releaseTarget) return;
    setWorking(releaseTarget.game_id); setError(null); setSuccess(null);
    try {
      await api(`/api/matches/${releaseTarget.game_id}/release`, { method: "POST" });
      setSuccess("任务已释放，署名已清空，正文仍然保留。");
      setReleaseTarget(null); await load(true);
    } catch (value) { setError(errorMessage(value)); } finally { setWorking(null); }
  };
  const assign = async () => {
    if (!assignTarget || !assignedUserId) return;
    setWorking(assignTarget.game_id); setError(null); setSuccess(null);
    try {
      await api(`/api/matches/${assignTarget.game_id}/assign`, { method: "POST", ...jsonBody({ user_id: Number(assignedUserId) }) });
      setSuccess("任务已转交，正文保持不变。");
      setAssignTarget(null); setAssignedUserId(""); await load(true);
    } catch (value) { setError(errorMessage(value)); } finally { setWorking(null); }
  };

  const card = (match: TaskMatch, source: "mine" | "waiting" | "open") => (
    <TaskCard
      key={`${source}-${match.game_id}`}
      match={match}
      claimantName={claimant(match)}
      canEnter={isAdmin || match.claimed_by_user_id === user?.id}
      claiming={working === match.game_id}
      onClaim={source === "waiting" ? () => void claim(match) : undefined}
      onRelease={(source === "mine" || (source === "open" && match.claimed_by_user_id !== null)) ? () => setReleaseTarget(match) : undefined}
      onAssign={isAdmin && source === "open" ? () => { setAssignTarget(match); setAssignedUserId(match.claimed_by_user_id ? String(match.claimed_by_user_id) : ""); } : undefined}
    />
  );

  if (loading) return <LoadingScreen label="正在读取任务" />;
  return (
    <>
      <PageHeader eyebrow="协作" title="任务中心" description="领取比赛后即可进入比赛页面填写前瞻正文。" />
      {error ? <Alert tone="danger" onDismiss={() => setError(null)}>{error}</Alert> : null}
      {success ? <Alert tone="success" onDismiss={() => setSuccess(null)}>{success}</Alert> : null}
      <Panel className="filter-bar task-filter">
        <label><span>开球日期</span><input type="date" value={date} onChange={(event) => setDate(event.target.value)} /></label>
        <label><span>赛事</span><select value={competition} onChange={(event) => setCompetition(event.target.value as Competition | "")}><option value="">全部赛事</option><option value="male">男足</option><option value="female">女足</option><option value="futsal">五人制</option></select></label>
        <Button onClick={() => { setDate(""); setCompetition(""); }}><Search size={16} />清除筛选</Button>
      </Panel>
      <div className="task-sections">
        <TaskSection
          title="我的任务"
          description="默认只显示当前开放任务；显示全部时，开放任务排在前面。"
          items={filteredMine}
          render={(match) => card(match, "mine")}
          actions={<label className="task-visibility-toggle"><input type="checkbox" checked={showUnavailableTasks} onChange={(event) => setShowUnavailableTasks(event.target.checked)} /><span>显示未开放任务</span></label>}
        />
        <TaskSection title="待领取任务" description="当前有效、开放且尚未认领的任务。" items={filteredWaiting} render={(match) => card(match, "waiting")} />
        {isAdmin ? <TaskSection title="全部开放任务" description="管理员可以查看、转交或释放全部当前开放任务。" items={filteredOpen} render={(match) => card(match, "open")} /> : null}
      </div>
      {releaseTarget ? <Modal title="确认释放任务" onClose={() => setReleaseTarget(null)} actions={<><Button onClick={() => setReleaseTarget(null)}>取消</Button><Button variant="danger" loading={working === releaseTarget.game_id} onClick={() => void release()}>确认释放</Button></>}><Alert tone="warning">释放后认领人和署名会被清空，已经填写的正文会保留。</Alert><p>{teamName(releaseTarget.home)} vs {teamName(releaseTarget.away)}</p></Modal> : null}
      {assignTarget ? <Modal title="转交任务" onClose={() => setAssignTarget(null)} actions={<><Button onClick={() => setAssignTarget(null)}>取消</Button><Button variant="primary" loading={working === assignTarget.game_id} disabled={!assignedUserId} onClick={() => void assign()}>确认转交</Button></>}><div className="stack"><p>{teamName(assignTarget.home)} vs {teamName(assignTarget.away)}</p><label className="field"><span>目标用户</span><select value={assignedUserId} onChange={(event) => setAssignedUserId(event.target.value)}><option value="">请选择启用账号</option>{users.filter((item) => item.is_active).map((item) => <option value={item.id} key={item.id}>{item.display_name}（@{item.username}）</option>)}</select></label></div></Modal> : null}
    </>
  );
}
