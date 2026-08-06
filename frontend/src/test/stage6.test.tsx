import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { AuthProvider } from "../auth";
import { ReportsPage } from "../pages/ReportsPage";
import type { PreviewBatch, PreviewMatch, User } from "../types";

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
    report: { available: status === "finished", kind: status === "finished" ? "image" : null, content_sha256: null, rendered_at: null },
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

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("stage 6 report batches", () => {
  it("expands from stored data and hides unfinished matches by default", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      return Promise.resolve(json(path.endsWith("/api/batches/7") ? detail : { items: [{ ...detail, matches: [] }] }));
    });
    vi.stubGlobal("fetch", fetchMock);
    const router = createMemoryRouter([{ path: "/reports", element: <ReportsPage /> }], { initialEntries: ["/reports"] });
    render(<AuthProvider initialUser={admin}><RouterProvider router={router} /></AuthProvider>);

    expect(await screen.findByRole("link", { name: "预览战报文章" })).toHaveAttribute("href", "/reports/7/article");
    expect(screen.queryByRole("button", { name: "渲染文章" })).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: /8月8日/ }));
    expect(await screen.findByRole("link", { name: /环境 vs 探微/ })).toHaveAttribute("href", "/reports/7/matches/101");
    expect(screen.queryByRole("link", { name: /环境 vs 行健/ })).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes("refresh-data"))).toBe(false);

    fireEvent.click(screen.getByRole("checkbox"));
    expect(screen.getByRole("link", { name: /环境 vs 行健/ })).toBeInTheDocument();
    expect(screen.queryByText("2:1")).not.toBeInTheDocument();
  });
});
