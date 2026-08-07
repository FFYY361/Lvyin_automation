import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { AuthProvider } from "../auth";
import { MatchPage } from "../pages/MatchPage";
import { BatchDetailPage } from "../pages/BatchDetailPage";
import { TasksPage } from "../pages/TasksPage";
import { UsersPage } from "../pages/UsersPage";
import type { PreviewBatch, TaskMatch, User } from "../types";

const admin: User = { id: 1, username: "admin", display_name: "管理员", role: "admin", is_active: true };
const member: User = { id: 2, username: "member", display_name: "成员甲", role: "user", is_active: true };
const task: TaskMatch = {
  game_id: 11, batch_id: 5, tournament_id: 122, tournament_name: "马杯男足", competition_name: "男足甲级", competition: "male", stage: "小组赛",
  kickoff: "2026-08-08T15:30:00+08:00", venue: "紫荆足球场",
  home: { team_id: 1, name: "环境学院", short_name: "环境", previous_outcomes: [], current_results: [] },
  away: { team_id: 2, name: "探微书院", short_name: "探微", previous_outcomes: [], current_results: [] },
  head_to_head: [], active: true, task_open: true, claimed_by_user_id: 2, writers: ["成员甲"], body: "已经填写", body_version: 3, updated_at: "2026-08-01T00:00:00Z",
  status: "scheduled", report: { available: false, content_sha256: null, rendered_at: null },
};

function namedTask(gameId: number, name: string, kickoff: string, overrides: Partial<TaskMatch> = {}): TaskMatch {
  return {
    ...task,
    ...overrides,
    game_id: gameId,
    kickoff,
    home: { ...task.home, name, short_name: name },
  };
}

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}
function renderPage(element: React.ReactNode, user: User, path = "/tasks", routePath = "/tasks") {
  const router = createMemoryRouter([{ path: routePath, element }], { initialEntries: [path] });
  render(<AuthProvider initialUser={user}><RouterProvider router={router} /></AuthProvider>);
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("stage 5 task center", () => {
  it("keeps the required section order and omits body/version metadata", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      if (path === "/api/me/tasks") return Promise.resolve(json({ items: [task] }));
      if (path === "/api/tasks/wait_claim") return Promise.resolve(json({ items: [{ ...task, game_id: 12, claimed_by_user_id: null }] }));
      if (path === "/api/tasks/open") return Promise.resolve(json({ items: [task] }));
      if (path === "/api/admin/users") return Promise.resolve(json({ items: [] }));
      return Promise.resolve(json({ id: 2, display_name: "成员甲" }));
    }));
    renderPage(<TasksPage />, admin);
    const mine = await screen.findByRole("heading", { name: "我的任务（1）" });
    const waiting = screen.getByRole("heading", { name: "待领取任务（1）" });
    const open = screen.getByRole("heading", { name: "全部开放任务（1）" });
    expect(mine.compareDocumentPosition(waiting) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
    expect(waiting.compareDocumentPosition(open) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
    expect(screen.queryByText(/已编辑正文|未编辑正文|保存版本|最近更新/)).not.toBeInTheDocument();
    expect(screen.getAllByText(/认领人：/).length).toBeGreaterThan(0);
  });

  it("does not request or show all open tasks for a normal user", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => Promise.resolve(json({ items: String(input) === "/api/me/tasks" ? [task] : [] })));
    vi.stubGlobal("fetch", fetchMock);
    renderPage(<TasksPage />, member);
    expect(await screen.findByRole("heading", { name: "我的任务（1）" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /全部开放任务/ })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([input]) => String(input) === "/api/tasks/open")).toBe(false);
  });

  it("sorts every section and hides unavailable owned tasks by default", async () => {
    const mine = [
      namedTask(21, "未开放较新", "2026-09-01T10:00:00+08:00", { task_open: false }),
      namedTask(22, "我的开放较旧", "2026-08-08T10:00:00+08:00"),
      namedTask(23, "失效较旧", "2026-08-20T10:00:00+08:00", { active: false }),
      namedTask(24, "我的开放较新", "2026-08-10T10:00:00+08:00"),
    ];
    const waiting = [
      namedTask(31, "待领取较旧", "2026-08-01T10:00:00+08:00", { claimed_by_user_id: null }),
      namedTask(32, "待领取较新", "2026-08-11T10:00:00+08:00", { claimed_by_user_id: null }),
    ];
    const open = [
      namedTask(41, "全部较旧", "2026-08-02T10:00:00+08:00"),
      namedTask(42, "全部较新", "2026-08-12T10:00:00+08:00"),
    ];
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      if (String(input) === "/api/me/tasks") return Promise.resolve(json({ items: mine }));
      if (String(input) === "/api/tasks/wait_claim") return Promise.resolve(json({ items: waiting }));
      if (String(input) === "/api/tasks/open") return Promise.resolve(json({ items: open }));
      return Promise.resolve(json({ items: [] }));
    }));
    renderPage(<TasksPage />, admin);

    const mineSection = (await screen.findByRole("heading", { name: "我的任务（2）" })).closest(".task-section") as HTMLElement;
    const waitingSection = screen.getByRole("heading", { name: "待领取任务（2）" }).closest(".task-section") as HTMLElement;
    const openSection = screen.getByRole("heading", { name: "全部开放任务（2）" }).closest(".task-section") as HTMLElement;
    const names = (section: HTMLElement) => within(section).getAllByRole("heading", { level: 3 }).map((heading) => heading.textContent?.split(" vs ")[0]);

    expect(names(mineSection)).toEqual(["我的开放较新", "我的开放较旧"]);
    expect(names(waitingSection)).toEqual(["待领取较新", "待领取较旧"]);
    expect(names(openSection)).toEqual(["全部较新", "全部较旧"]);
    expect(screen.queryByText("未开放较新", { exact: false })).not.toBeInTheDocument();

    fireEvent.click(screen.getByLabelText("显示未开放任务"));
    await screen.findByRole("heading", { name: "我的任务（4）" });
    expect(names(mineSection)).toEqual(["我的开放较新", "我的开放较旧", "未开放较新", "失效较旧"]);
  });
});

describe("stage 5 user management", () => {
  it("renders one full-width list row per user", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ items: [
      { ...admin, display_name: "系统管理员", created_at: "2026-08-01T00:00:00Z", updated_at: "2026-08-01T00:00:00Z", claimed_task_count: 0 },
      { ...member, created_at: "2026-08-01T00:00:00Z", updated_at: "2026-08-02T00:00:00Z", claimed_task_count: 3 },
    ] })));
    renderPage(<UsersPage />, admin, "/users", "/users");

    expect(await screen.findByText("系统管理员")).toBeInTheDocument();
    expect(document.querySelector(".user-list")).toBeInTheDocument();
    expect(document.querySelectorAll(".user-row")).toHaveLength(2);
    expect(document.querySelector(".user-grid")).not.toBeInTheDocument();
  });
});

describe("stage 5 match permissions", () => {
  it("uses the body-only endpoint for an owning normal user", async () => {
    const batch: PreviewBatch = {
      id: 5, batch_date: "2026-08-08", competition: "male", preview_status: "incomplete", headline: "前瞻", editors: [], reviewers: [], approvers: [],
      cover: { kind: "media_id", storage_key: "cover", content_type: null }, current_preview_article_id: null, latest_preview_article_id: null, current_report_article_id: null, latest_report_article_id: null, missing_fields: [], last_error: null,
      created_at: "2026-08-01T00:00:00Z", updated_at: "2026-08-01T00:00:00Z", matches: [task],
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "PATCH") return Promise.resolve(json({ game_id: 11, writers: ["成员甲"], body: "新正文", body_version: 4 }));
      return Promise.resolve(json(batch));
    });
    vi.stubGlobal("fetch", fetchMock);
    renderPage(<MatchPage />, member, "/previews/5/matches/11", "/previews/:batchId/matches/:gameId");
    fireEvent.change(await screen.findByLabelText("前瞻正文"), { target: { value: "新正文" } });
    fireEvent.click(screen.getByRole("button", { name: "保存正文" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) => String(input) === "/api/matches/11/body" && init?.method === "PATCH")).toBe(true));
    expect(screen.getByLabelText("署名")).toHaveAttribute("readonly");
  });

  it("blocks a normal user from opening another member's match", async () => {
    const batch = { id: 5, matches: [{ ...task, claimed_by_user_id: 9 }] } as unknown as PreviewBatch;
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(batch)));
    renderPage(<MatchPage />, member, "/previews/5/matches/11", "/previews/:batchId/matches/:gameId");
    expect(await screen.findByRole("heading", { name: "无法进入比赛" })).toBeInTheDocument();
    expect(screen.queryByLabelText("前瞻正文")).not.toBeInTheDocument();
  });
});

describe("stage 5 batch permissions", () => {
  it("shows claimant names but only links a normal user to their own match", async () => {
    const other = { ...task, game_id: 12, claimed_by_user_id: 9 };
    const batch = {
      id: 5, batch_date: "2026-08-08", competition: "male", preview_status: "incomplete", headline: "前瞻", editors: [], reviewers: [], approvers: [],
      cover: { kind: "media_id", storage_key: "cover", content_type: null }, current_preview_article_id: null, latest_preview_article_id: null, current_report_article_id: null, latest_report_article_id: null, missing_fields: [], last_error: null,
      created_at: "2026-08-01T00:00:00Z", updated_at: "2026-08-01T00:00:00Z", matches: [task, other],
    } satisfies PreviewBatch;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => Promise.resolve(json(String(input).includes("/api/admin/users/9") ? { id: 9, display_name: "成员乙" } : batch))));
    renderPage(<BatchDetailPage />, member, "/previews/5", "/previews/:batchId");
    expect((await screen.findAllByText("认领人：", { selector: ".match-card__meta span" }))).toHaveLength(2);
    expect(await screen.findByText("成员乙")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /环境 vs 探微/ })).toHaveAttribute("href", "/previews/5/matches/11");
    expect(screen.getAllByRole("link", { name: /环境 vs 探微/ })).toHaveLength(1);
  });
});
