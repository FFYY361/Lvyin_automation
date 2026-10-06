import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, Save, Search } from "lucide-react";
import { Link, useBlocker, useParams } from "react-router-dom";
import { api, errorMessage, jsonBody } from "../api";
import { Alert, Badge, Button, Field, LoadingScreen, Modal, PageHeader, Panel, SectionTitle } from "../components";
import { competitionLabels, type Competition, type InstitutionDetail } from "../types";

const teamFields = [
  ["male", "male_description", "male_team_ids"],
  ["female", "female_description", "female_team_ids"],
  ["futsal", "futsal_description", "futsal_team_ids"],
] as const;

export function InstitutionPage() {
  const { institutionName = "" } = useParams();
  const [value, setValue] = useState<InstitutionDetail | null>(null);
  const [baseValue, setBaseValue] = useState<InstitutionDetail | null>(null);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const dirty = value !== null && baseValue !== null && JSON.stringify(value) !== JSON.stringify(baseValue);
  const blocker = useBlocker(dirty);

  useEffect(() => {
    document.title = "院系资料 · 绿茵宣传部";
    setLoading(true);
    api<InstitutionDetail>(`/api/admin/institutions/${encodeURIComponent(institutionName)}`)
      .then((result) => { setValue(result); setBaseValue(result); })
      .catch((reason) => setError(errorMessage(reason)))
      .finally(() => setLoading(false));
  }, [institutionName]);

  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => { if (dirty) { event.preventDefault(); event.returnValue = ""; } };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [dirty]);

  const filteredPlayers = useMemo(() => {
    if (!value) return [];
    const search = query.trim().toLocaleLowerCase("zh-CN");
    if (!search) return value.players;
    return value.players.filter((player) => player.name.toLocaleLowerCase("zh-CN").includes(search));
  }, [query, value]);

  const save = async () => {
    if (!value) return;
    setSaving(true); setError(null); setSuccess(null);
    try {
      const result = await api<InstitutionDetail>(`/api/admin/institutions/${encodeURIComponent(value.name)}`, {
        method: "PUT",
        ...jsonBody({
          male_description: value.male_description,
          female_description: value.female_description,
          futsal_description: value.futsal_description,
          player_descriptions: Object.fromEntries(value.players.map((player) => [player.name, player.description])),
        }),
      });
      setValue(result); setBaseValue(result); setSuccess("院系人工资料已保存。");
    } catch (reason) { setError(errorMessage(reason)); } finally { setSaving(false); }
  };

  const updatePlayer = (name: string, description: string) => {
    setValue((current) => current ? { ...current, players: current.players.map((player) => player.name === name ? { ...player, description } : player) } : current);
  };

  if (loading) return <LoadingScreen label="正在读取院系资料" />;
  if (!value) return <><PageHeader title="院系资料不存在" actions={<Link className="button button--quiet" to="/settings"><ArrowLeft size={16} />返回资料管理</Link>} /><Alert tone="danger">{error || "无法读取院系资料"}</Alert></>;

  return (
    <>
      <PageHeader eyebrow={value.short_name} title={value.name} description="球队 ID、球员姓名和项目归属由数据库同步维护，本页只编辑人工描述。" actions={<><Link className="button button--quiet" to="/settings"><ArrowLeft size={16} />返回资料管理</Link><Button variant="primary" loading={saving} disabled={!dirty} onClick={() => void save()}><Save size={16} />保存全部资料</Button></>} />
      {error ? <Alert tone="danger" onDismiss={() => setError(null)}>{error}</Alert> : null}
      {success ? <Alert tone="success" onDismiss={() => setSuccess(null)}>{success}</Alert> : null}

      {value.predecessors?.length ? <Panel>
        <SectionTitle title="前身球队" description="历史比赛保留前身身份，人工描述统一由本院系维护。" />
        {value.predecessors.map((predecessor) => <div key={predecessor.name}>
          <strong>{predecessor.name}（{predecessor.short_name}）</strong>
          {teamFields.map(([competition, , idsField]) => <p key={competition}>{competitionLabels[competition]}：{predecessor[idsField].join("、") || "无球队 ID"}</p>)}
        </div>)}
      </Panel> : null}

      <Panel>
        <SectionTitle title="球队描述" description="维护该院系现有比赛项目的整体特点。" />
        <div className="institution-team-editor-grid">
          {teamFields
            .filter(([, , idsField]) => value[idsField].length > 0 || value.predecessors?.some((item) => item[idsField].length > 0))
            .map(([competition, descriptionField, idsField]) => <Field key={competition} label={competitionLabels[competition]} hint={`本体球队 ID：${value[idsField].join("、") || "尚未登记"}`}><textarea rows={8} value={value[descriptionField]} onChange={(event) => setValue({ ...value, [descriptionField]: event.target.value })} /></Field>)}
        </div>
      </Panel>

      <Panel className="data-management-panel">
        <SectionTitle title="球员描述" description={`共 ${value.players.length} 人；姓名在各自球队内作为唯一标识。`} />
        <div className="institution-search"><Search size={17} /><input aria-label="搜索球员" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索球员姓名" /></div>
        <div className="institution-player-list">
          {filteredPlayers.map((player) => <div className="institution-player-row" key={player.name}><div><strong>{player.name}</strong><span>{player.competitions.map((competition: Competition) => <Badge key={competition}>{competitionLabels[competition]}</Badge>)}</span></div><textarea aria-label={`${player.name}的描述`} rows={3} value={player.description} onChange={(event) => updatePlayer(player.name, event.target.value)} /></div>)}
        </div>
        {!filteredPlayers.length ? <p className="history-empty">没有匹配的球员。</p> : null}
      </Panel>

      {blocker.state === "blocked" ? <Modal title="有未保存的院系资料" actions={<><Button onClick={() => blocker.reset()}>留在此页</Button><Button variant="danger" onClick={() => blocker.proceed()}>放弃修改并离开</Button></>}><p>离开页面会丢失尚未保存的球队或球员描述。</p></Modal> : null}
    </>
  );
}
