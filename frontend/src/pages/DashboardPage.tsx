import { useEffect, useMemo, useState, type FormEvent } from "react";
import { ArrowRight, CalendarDays, Plus, X } from "lucide-react";
import { Link } from "react-router-dom";
import { ApiError, api, errorMessage, jsonBody } from "../api";
import { Alert, Badge, Button, EmptyState, Field, PageHeader, Panel, SectionTitle } from "../components";
import { competitionLabels, type Competition, type CreateBatchResult } from "../types";
import { cartesianPairs, formatDate } from "../utils";

const competitions: Competition[] = ["male", "female", "futsal"];

export function DashboardPage() {
  const [dateInput, setDateInput] = useState("");
  const [dates, setDates] = useState<string[]>([]);
  const [selectedCompetitions, setSelectedCompetitions] = useState<Competition[]>(["male", "female"]);
  const [results, setResults] = useState<CreateBatchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { document.title = "创建批次 · 前瞻管理"; }, []);
  const combinations = useMemo(
    () => cartesianPairs(dates, selectedCompetitions).map(({ left: date, right: competition }) => ({ date, competition })),
    [dates, selectedCompetitions],
  );

  const addDate = () => {
    if (!dateInput) return;
    setDates((current) => Array.from(new Set([...current, dateInput])).sort());
    setDateInput("");
  };

  const toggleCompetition = (competition: Competition) => {
    setSelectedCompetitions((current) =>
      current.includes(competition) ? current.filter((item) => item !== competition) : competitions.filter((item) => [...current, competition].includes(item)),
    );
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!dates.length || !selectedCompetitions.length) return;
    setLoading(true);
    setError(null);
    setResults([]);
    try {
      const response = await api<{ results: CreateBatchResult[] }>("/api/batches/create", {
        method: "POST",
        ...jsonBody({ dates, competitions: selectedCompetitions }),
      });
      setResults(response.results);
    } catch (value) {
      if (value instanceof ApiError && Array.isArray(value.details?.results)) {
        setResults(value.details.results as unknown as CreateBatchResult[]);
      }
      setError(errorMessage(value));
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <PageHeader eyebrow="工作台" title="创建前瞻批次" description="选择日期和赛事，在一次查询中创建所有组合。" />
      <div className="two-column two-column--wide">
        <Panel>
          <SectionTitle title="选择范围" description="日期与赛事将组合成独立批次。" />
          {error ? <Alert tone="danger" onDismiss={() => setError(null)}>{error}</Alert> : null}
          <form onSubmit={submit} className="stack stack--large">
            <Field label="比赛日期" hint="最多可以创建 31 个日期。">
              <div className="inline-field">
                <input type="date" value={dateInput} onChange={(event) => setDateInput(event.target.value)} />
                <Button type="button" onClick={addDate} disabled={!dateInput}><Plus size={16} />添加</Button>
              </div>
            </Field>
            <div className="chip-list" aria-label="已选日期">
              {dates.map((date) => (
                <span className="chip" key={date}>{formatDate(date)}<button type="button" onClick={() => setDates((items) => items.filter((item) => item !== date))} aria-label={`移除 ${date}`}><X size={14} /></button></span>
              ))}
              {!dates.length ? <span className="muted">尚未添加日期</span> : null}
            </div>
            <fieldset className="field-group">
              <legend>赛事</legend>
              <div className="option-grid">
                {competitions.map((competition) => (
                  <label className="check-card" key={competition}>
                    <input type="checkbox" checked={selectedCompetitions.includes(competition)} onChange={() => toggleCompetition(competition)} />
                    <span>{competitionLabels[competition]}</span>
                    <small>{competition}</small>
                  </label>
                ))}
              </div>
            </fieldset>
            <Button variant="primary" loading={loading} type="submit" disabled={!combinations.length}>创建 {combinations.length || ""} 个批次</Button>
          </form>
        </Panel>
        <Panel>
          <SectionTitle title="即将创建" description={`${dates.length} 个日期 × ${selectedCompetitions.length} 个赛事`} />
          {!combinations.length ? (
            <EmptyState title="等待选择" description="添加日期并至少选择一个赛事。" />
          ) : (
            <div className="combination-list">
              {combinations.map(({ date, competition }) => (
                <div className="combination-row" key={`${date}-${competition}`}>
                  <CalendarDays size={17} /><span>{formatDate(date)}</span><Badge>{competitionLabels[competition]}</Badge>
                </div>
              ))}
            </div>
          )}
        </Panel>
      </div>
      {results.length ? (
        <Panel className="results-panel">
          <SectionTitle title="创建结果" actions={<Link className="text-link" to="/previews">查看全部批次 <ArrowRight size={15} /></Link>} />
          <div className="result-grid">
            {results.map((result) => (
              <article className="result-card" key={`${result.date}-${result.competition}`}>
                <div><strong>{formatDate(result.date)}</strong><span>{competitionLabels[result.competition]}</span></div>
                <Badge tone={result.status === "failed" ? "danger" : result.status === "created" ? "success" : result.status === "skipped" ? "warning" : "neutral"}>{result.status}</Badge>
                {result.warning ? <p>{result.warning}</p> : null}
                {result.error ? <p className="danger-text">{result.error.message}</p> : null}
                {result.batch_id ? <Link to={`/previews/${result.batch_id}`}>打开批次 <ArrowRight size={14} /></Link> : null}
              </article>
            ))}
          </div>
        </Panel>
      ) : null}
    </>
  );
}
