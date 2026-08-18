import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { AuthProvider } from "../auth";
import { ReportsPage } from "../pages/ReportsPage";
import { BatchesPage } from "../pages/BatchesPage";
import { ReportDiagnostics } from "../ReportDiagnostics";
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
    home: { team_id: 1, name: "环境学院", short_name: "环境", previous_outcomes: [], current_results: [] },
    away: { team_id: 2, name: `${away}学院`, short_name: away, previous_outcomes: [], current_results: [] },
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
  vi.unstubAllGlobals();
});

describe("stage 6 report batches", () => {
  it("groups concrete report errors and warnings by match", () => {
    const diagnostics: ReportRenderDiagnostic[] = [
      { game_id: 101, status: "failed", reused: null, error: { code: "report_event_validation_failed", message: "validation failed" }, issues: [{ severity: "error", code: "duplicate_start", message: "Player appears in more than one START event.", event_ids: [11, 12], player_id: 99, side: "home", minute: 0, stoppage_minute: 0 }] },
      { game_id: 102, status: "success", reused: false, error: null, issues: [{ severity: "warning", code: "lineup_under_capacity", message: "away has 10 START players; expected 11.", event_ids: [], player_id: null, side: "away", minute: null, stoppage_minute: null }] },
    ];
    render(<ReportDiagnostics diagnostics={diagnostics} matches={detail.matches} />);

    expect(screen.getByText("环境 vs 探微")).toBeInTheDocument();
    expect(screen.getByText("球员重复出现在首发事件中")).toBeInTheDocument();
    expect(screen.getByText(/球员 #99 · 事件 #11、#12/)).toBeInTheDocument();
    expect(screen.getByText("环境 vs 行健")).toBeInTheDocument();
    expect(screen.getByText("首发人数不足")).toBeInTheDocument();
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
    const warning = { severity: "warning", code: "lineup_under_capacity", message: "home has 10 START players; expected 11.", event_ids: [], player_id: null, side: "home", minute: null, stoppage_minute: null } as const;
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
    expect(screen.getByText("环境 vs 探微")).toBeInTheDocument();
    expect(screen.getByText("首发人数不足")).toBeInTheDocument();
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

    const buttons = await screen.findAllByRole("button", { name: "渲染文章" });
    fireEvent.click(buttons[0]);
    fireEvent.click(buttons[1]);
    await waitFor(() => expect(buttons.every((button) => button.hasAttribute("disabled"))).toBe(true));

    await act(async () => firstRender.resolve(json({ reused: false, article: {}, diagnostics: [] })));
    await waitFor(() => expect(buttons[0]).not.toBeDisabled());
    expect(buttons[1]).toBeDisabled();

    await act(async () => secondRender.resolve(json({ reused: false, article: {}, diagnostics: [] })));
    await waitFor(() => expect(buttons[1]).not.toBeDisabled());
  });

  it("keeps preview batch loading states independent", async () => {
    const second = { ...detail, id: 8, batch_date: "2026-08-09", matches: [] };
    const firstRender = deferred<Response>();
    const secondRender = deferred<Response>();
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (init?.method === "POST") return path.endsWith("/7/render-preview") ? firstRender.promise : secondRender.promise;
      return Promise.resolve(json({ items: [{ ...detail, matches: [] }, second] }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const router = createMemoryRouter([{ path: "/previews", element: <BatchesPage /> }], { initialEntries: ["/previews"] });
    render(<AuthProvider initialUser={admin}><RouterProvider router={router} /></AuthProvider>);

    const buttons = await screen.findAllByRole("button", { name: "渲染文章" });
    fireEvent.click(buttons[0]);
    fireEvent.click(buttons[1]);
    await waitFor(() => expect(buttons.every((button) => button.hasAttribute("disabled"))).toBe(true));

    const article = { missing_fields: [] };
    await act(async () => firstRender.resolve(json({ reused: false, article })));
    await waitFor(() => expect(buttons[0]).not.toBeDisabled());
    expect(buttons[1]).toBeDisabled();

    await act(async () => secondRender.resolve(json({ reused: false, article })));
    await waitFor(() => expect(buttons[1]).not.toBeDisabled());
  });
});
