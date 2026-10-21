import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { AuthProvider } from "../auth";
import { ReportsPage } from "../pages/ReportsPage";
import { BatchesPage } from "../pages/BatchesPage";
import { ReportDiagnostics, reportBatchFeedback } from "../ReportDiagnostics";
import type { PreviewBatch, PreviewMatch, ReportRenderDiagnostic, User } from "../types";

const admin: User = {
  id: 1,
  username: "admin",
  display_name: "管理员",
  role: "admin",
  is_active: true,
};

function match(gameId: number, status: PreviewMatch["status"], away: string): PreviewMatch {
  return {
    game_id: gameId,
    batch_id: 7,
    tournament_id: 10,
    tournament_name: "马约翰杯",
    competition_name: "男足甲级",
    stage: "淘汰赛",
    kickoff: `2026-08-08T${gameId === 101 ? "15" : "17"}:00:00+08:00`,
    venue: "紫荆足球场",
    home: { tournament_team_id: 1, name: "环境学院", short_name: "环境", previous_outcomes: [], current_results: [] },
    away: { tournament_team_id: 2, name: `${away}学院`, short_name: away, previous_outcomes: [], current_results: [] },
    head_to_head: [],
    active: true,
    task_open: false,
    claimed_by_user_id: null,
    writers: [],
    body: "",
    body_version: 0,
    status,
    report: { available: status === "finished", content_sha256: null, rendered_at: null },
    updated_at: "2026-08-01T00:00:00Z",
  };
}

const detail: PreviewBatch = {
  id: 7,
  batch_date: "2026-08-08",
  competition: "male",
  preview_status: "ready",
  headline: "周末前瞻",
  editors: [],
  reviewers: [],
  approvers: [],
  cover: { kind: "media_id", storage_key: "cover", content_type: null },
  current_preview_article_id: null,
  latest_preview_article_id: null,
  current_report_article_id: null,
  latest_report_article_id: null,
  missing_fields: [],
  last_error: null,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
  weather: null,
  matches: [match(101, "finished", "探微"), match(102, "scheduled", "行健")],
};

function json(value: unknown) {
  return new Response(JSON.stringify(value), { status: 200, headers: { "Content-Type": "application/json" } });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("batch date groups", () => {
  it.each([
    { path: "/previews", Page: BatchesPage, label: "历史批次", link: "打开批次", current: [3, 6, 5, 2], collapsed: [4, 1] },
    { path: "/reports", Page: ReportsPage, label: "未来批次", link: "预览战报文章", current: [3, 6, 4, 1], collapsed: [5, 2] },
  ])("sorts $path around today and toggles $label", async ({ path, Page, label, link, current, collapsed }) => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2025-12-31T16:30:00Z"));
    let items = [
      { ...detail, id: 2, batch_date: "2026-01-03" },
      { ...detail, id: 1, batch_date: "2025-12-30" },
      { ...detail, id: 3, batch_date: "2026-01-01" },
      { ...detail, id: 5, batch_date: "2026-01-02" },
      { ...detail, id: 6, batch_date: "2026-01-01", competition: "female" as const },
      { ...detail, id: 4, batch_date: "2025-12-31" },
    ];
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(json({ items }))));
    const router = createMemoryRouter([{ path, element: <Page /> }], { initialEntries: [path] });
    render(<AuthProvider initialUser={admin}><RouterProvider router={router} /></AuthProvider>);
    const ids = () => screen.queryAllByRole("link", { name: link }).map((item) => Number(item.getAttribute("href")!.split("/")[2]));

    const toggle = await screen.findByRole("button", { name: new RegExp(label) });
    expect(ids()).toEqual(current);
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(ids()).toEqual([...current, ...collapsed]);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(toggle);
    expect(ids()).toEqual(current);

    items = items.filter((item) => collapsed.includes(item.id));
    fireEvent.click(screen.getByRole("button", { name: "查询" }));
    const onlyGroup = await screen.findByRole("button", { name: new RegExp(label) });
    expect(ids()).toEqual([]);
    fireEvent.click(onlyGroup);
    expect(ids()).toEqual(collapsed);
  });
});

describe("stage 6 report batches", () => {
  it("summarizes warning matches and technical failures separately", () => {
    const issue = { severity: "warning", code: "duplicate_start", message: "球员 8号张三在首发中重复出现", event_ids: [11], player_id: 99, side: "home", minute: 0, stoppage_minute: 0 } as const;
    const diagnostics: ReportRenderDiagnostic[] = [
      { game_id: 101, status: "success", reused: true, error: null, issues: [{ ...issue, event_ids: [11] }, { ...issue, event_ids: [12] }] },
    ];
    expect(reportBatchFeedback(diagnostics)).toEqual({ tone: "warning", message: "单场战报已生成并保存；有1场比赛需要核对，暂未生成批次文章。" });
    diagnostics.push({ game_id: 102, status: "failed", reused: null, issues: [], error: { code: "report_query_failed", message: "query unavailable" } });
    expect(reportBatchFeedback(diagnostics)).toEqual({ tone: "danger", message: "单场战报已生成并保存；有1场比赛需要核对，有1场比赛生成失败，暂未生成批次文章。" });
  });
  it("shows compact Chinese business diagnostics without database identifiers", () => {
    const diagnostics: ReportRenderDiagnostic[] = [
      { game_id: 101, status: "success", reused: false, error: null, issues: [{ severity: "warning", code: "duplicate_start", message: "球员 8号张三在首发中重复出现", team_name: "环境", time_label: "0 分钟", event_ids: [11, 12], player_id: 99, side: "home", minute: 0, stoppage_minute: 0 }] },
      { game_id: 102, status: "success", reused: false, error: null, issues: [{ severity: "info", code: "lineup_under_capacity", message: "仅10人首发，预期11人", team_name: "行健", time_label: "0 分钟", event_ids: [], player_id: null, side: "away", minute: null, stoppage_minute: null }] },
    ];
    render(<ReportDiagnostics diagnostics={diagnostics} matches={detail.matches} />);
    expect(screen.getByText("[警告] duplicate_start")).toBeInTheDocument();
    expect(screen.getByText("环境 · 0 分钟 · 球员 8号张三在首发中重复出现")).toBeInTheDocument();
    expect(screen.getByText("[信息] lineup_under_capacity")).toBeInTheDocument();
    expect(screen.getByText("行健 · 0 分钟 · 仅10人首发，预期11人")).toBeInTheDocument();
    expect(screen.queryByText(/球员 #99|事件 #11|渲染失败/)).not.toBeInTheDocument();
  });

  it("expands from stored data and hides unfinished matches by default", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      return Promise.resolve(json(path.endsWith("/api/batches/7") ? detail : { items: [{ ...detail, matches: [] }] }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const router = createMemoryRouter([{ path: "/reports", element: <ReportsPage /> }], { initialEntries: ["/reports"] });
    render(<AuthProvider initialUser={admin}><RouterProvider router={router} /></AuthProvider>);

    expect(await screen.findByRole("link", { name: "预览战报文章" })).toHaveAttribute("href", "/reports/7/article");
    expect(screen.getByRole("button", { name: "渲染文章" })).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: /8月8日/ }));
    expect(await screen.findByRole("link", { name: /环境 vs 探微/ })).toHaveAttribute("href", "/reports/7/matches/101");
    expect(screen.queryByRole("link", { name: /环境 vs 行健/ })).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes("refresh-data"))).toBe(false);

    fireEvent.click(screen.getByRole("checkbox"));
    expect(screen.getByRole("link", { name: /环境 vs 行健/ })).toBeInTheDocument();
    expect(screen.queryByText("2:1")).not.toBeInTheDocument();
  });

  it("renders a report article from the batch row and shows match warnings", async () => {
    const warning = { severity: "info", code: "lineup_under_capacity", message: "仅10人首发，预期11人", team_name: "环境", time_label: "0 分钟", event_ids: [], player_id: null, side: "home", minute: null, stoppage_minute: null } as const;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (init?.method === "POST" && path.endsWith("/render-report")) return Promise.resolve(json({ reused: false, article: {}, diagnostics: [{ game_id: 101, status: "success", reused: false, issues: [warning], error: null }] }));
      return Promise.resolve(json(path.endsWith("/api/batches/7") ? detail : { items: [{ ...detail, matches: [] }] }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const router = createMemoryRouter([{ path: "/reports", element: <ReportsPage /> }], { initialEntries: ["/reports"] });
    render(<AuthProvider initialUser={admin}><RouterProvider router={router} /></AuthProvider>);

    fireEvent.click(await screen.findByRole("button", { name: "渲染文章" }));
    expect(await screen.findByText("战报文章渲染成功。")).toBeInTheDocument();
    expect(screen.getByText("环境 · 0 分钟 · 仅10人首发，预期11人")).toBeInTheDocument();
  });

  it("keeps report batch loading states independent", async () => {
    const second = { ...detail, id: 8, batch_date: "2026-08-09", matches: detail.matches?.map((item) => ({ ...item, batch_id: 8 })) };
    const firstRender = deferred<Response>();
    const secondRender = deferred<Response>();
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (init?.method === "POST") return path.endsWith("/7/render-report") ? firstRender.promise : secondRender.promise;
      if (path.endsWith("/api/batches/7")) return Promise.resolve(json(detail));
      if (path.endsWith("/api/batches/8")) return Promise.resolve(json(second));
      return Promise.resolve(json({ items: [{ ...detail, matches: [] }, { ...second, matches: [] }] }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const router = createMemoryRouter([{ path: "/reports", element: <ReportsPage /> }], { initialEntries: ["/reports"] });
    render(<AuthProvider initialUser={admin}><RouterProvider router={router} /></AuthProvider>);

    await screen.findAllByRole("button", { name: "渲染文章" });
    const buttons = [7, 8].map((id) => within(screen.getByText(`#${id}`).closest("tr")!).getByRole("button", { name: "渲染文章" }));
    fireEvent.click(buttons[0]);
    fireEvent.click(buttons[1]);
    await waitFor(() => expect(buttons.every((button) => button.hasAttribute("disabled"))).toBe(true));

    await act(async () => firstRender.resolve(json({ reused: false, article: {}, diagnostics: [] })));
    await waitFor(() => expect(buttons[0]).not.toBeDisabled());
    expect(buttons[1]).toBeDisabled();

    await act(async () => secondRender.resolve(json({ reused: false, article: {}, diagnostics: [] })));
    await waitFor(() => expect(buttons[1]).not.toBeDisabled());
  });

  it.each([true, false])("updates expanded match reports after batch rendering (success=%s)", async (success) => {
    let rendered = false;
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (init?.method === "POST") {
        rendered = true;
        return Promise.resolve(new Response(JSON.stringify(success
          ? { reused: false, article: {}, diagnostics: [] }
          : { detail: { code: "report_render_failed", message: "another match failed" } }),
        { status: success ? 200 : 502, headers: { "Content-Type": "application/json" } }));
      }
      if (path.endsWith("/api/batches/7")) return Promise.resolve(json({ ...detail,
        matches: detail.matches?.map((item) => ({ ...item, report: { ...item.report, available: rendered } })),
      }));
      return Promise.resolve(json({ items: [{ ...detail, matches: [] }] }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const router = createMemoryRouter([{ path: "/reports", element: <ReportsPage /> }], { initialEntries: ["/reports"] });
    render(<AuthProvider initialUser={admin}><RouterProvider router={router} /></AuthProvider>);

    fireEvent.click(await screen.findByRole("button", { name: /8月8日/ }));
    expect(await screen.findByText("尚未生成战报")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "渲染文章" }));
    expect(await screen.findByText("战报已生成")).toBeInTheDocument();
    expect(screen.queryByText("尚未生成战报")).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes("refresh-data"))).toBe(false);
  });

});
