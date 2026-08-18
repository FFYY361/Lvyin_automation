import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { AuthProvider } from "../auth";
import { BatchDetailPage } from "../pages/BatchDetailPage";
import { BatchesPage } from "../pages/BatchesPage";
import { MatchPage } from "../pages/MatchPage";
import { PreviewPage } from "../pages/PreviewPage";
import type { Article, PreviewBatch, User } from "../types";

const admin: User = { id: 99, username: "admin", display_name: "管理员", role: "admin", is_active: true };

const batch: PreviewBatch = {
  id: 1,
  batch_date: "2026-08-08",
  competition: "male",
  preview_status: "incomplete",
  headline: "周末前瞻",
  editors: ["编辑"],
  reviewers: ["责编"],
  approvers: ["审核"],
  cover: { kind: "media_id", storage_key: "cover", content_type: null },
  current_preview_article_id: null,
  latest_preview_article_id: null,
  current_report_article_id: null,
  latest_report_article_id: null,
  missing_fields: ["matches.11.body"],
  last_error: null,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
  weather: null,
  matches: [{
    game_id: 11,
    batch_id: 1,
    tournament_id: 122,
    tournament_name: "2025~2026 马杯男足甲级",
    competition_name: "男足甲级",
    stage: "决赛",
    kickoff: "2026-08-08T15:30:00+08:00",
    venue: "紫荆足球场",
    home: {
      team_id: 1,
      name: "环境学院",
      short_name: "环境",
      previous_outcomes: [{ season: "2024~2025", competition_label: "甲", outcome: "八强" }],
      current_results: [],
    },
    away: {
      team_id: 2,
      name: "探微书院",
      short_name: "探微",
      previous_outcomes: [{ season: "2024~2025", competition_label: null, outcome: "未参赛" }],
      current_results: [],
    },
    head_to_head: [],
    active: true,
    task_open: true,
    claimed_by_user_id: null,
    writers: ["作者"],
    body: "正文",
    body_version: 2,
    status: "scheduled",
    report: { available: false, content_sha256: null, rendered_at: null },
    updated_at: "2026-08-01T00:00:00Z",
  }],
};

function json(value: unknown) {
  return new Response(JSON.stringify(value), { status: 200, headers: { "Content-Type": "application/json" } });
}

function renderRoute(path: string, routePath: string, element: React.ReactNode) {
  const router = createMemoryRouter([{ path: routePath, element }], { initialEntries: [path] });
  render(<AuthProvider initialUser={admin}><RouterProvider router={router} /></AuthProvider>);
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("batch and match pages", () => {
  it("hides futsal batches until explicitly enabled", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({ items: [batch, { ...batch, id: 2, competition: "futsal" }] })));
    renderRoute("/previews", "/previews", <BatchesPage />);

    expect(await screen.findAllByRole("link", { name: "打开批次" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "渲染文章" })).toHaveLength(1);
    fireEvent.click(screen.getByRole("checkbox", { name: "显示五人制批次" }));
    expect(screen.getAllByRole("link", { name: "打开批次" })).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: "渲染文章" })).toHaveLength(2);
  });

  it("renders a preview article from the batch row and shows missing-field warnings", async () => {
    vi.stubGlobal("fetch", vi.fn((_input: RequestInfo | URL, init?: RequestInit) => Promise.resolve(json(init?.method === "POST" ? { reused: false, article: { missing_fields: ["matches.11.body"] } } : { items: [batch] }))));
    renderRoute("/previews", "/previews", <BatchesPage />);

    fireEvent.click(await screen.findByRole("button", { name: "渲染文章" }));
    expect(await screen.findByText("前瞻文章已生成，但仍存在缺项。")).toBeInTheDocument();
    expect(screen.getByText("比赛 #11 · 正文")).toBeInTheDocument();
  });

  it("uses compact match cards as links from the batch page", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(batch)));
    renderRoute("/previews/1", "/previews/:batchId", <BatchDetailPage />);
    const matchHeading = await screen.findByRole("heading", { name: "比赛" });
    const completenessHeading = screen.getByRole("heading", { name: "完整性缺项" });
    const headlineHeading = screen.getByRole("heading", { name: "文章标题" });
    const settingsHeading = screen.getByRole("heading", { name: "管理设置" });
    expect(screen.getByRole("heading", { name: "2026-08-08 · 男足" })).toBeInTheDocument();
    expect(screen.getByText("周末前瞻")).toBeInTheDocument();
    expect(screen.getByText("当前状态")).toBeInTheDocument();
    expect(screen.getByText("有效比赛")).toBeInTheDocument();
    expect(screen.getByText("开放任务")).toBeInTheDocument();
    expect(screen.queryByText("最近文章")).not.toBeInTheDocument();
    expect(screen.getByText("1 场比赛，当前开放 1/1 场有效比赛。")).toBeInTheDocument();
    expect(completenessHeading.compareDocumentPosition(headlineHeading) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
    expect(headlineHeading.compareDocumentPosition(matchHeading) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
    expect(matchHeading.compareDocumentPosition(settingsHeading) & Node.DOCUMENT_POSITION_FOLLOWING).not.toBe(0);
    expect(screen.getByText("环境 vs 探微 · 正文")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /环境 vs 探微/ })).toHaveAttribute("href", "/previews/1/matches/11");
    expect(screen.queryByLabelText("前瞻正文")).not.toBeInTheDocument();
  });

  it("saves the headline and personnel as separate batch patches", async () => {
    const fetchMock = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) => Promise.resolve(json(batch)));
    vi.stubGlobal("fetch", fetchMock);
    renderRoute("/previews/1", "/previews/:batchId", <BatchDetailPage />);

    fireEvent.change(await screen.findByLabelText("标题内容"), { target: { value: "新的文章标题" } });
    fireEvent.click(screen.getByRole("button", { name: "保存标题" }));
    await screen.findByText("文章标题已保存");

    const personnelPanel = screen.getByRole("heading", { name: "批次信息" }).closest("section");
    expect(personnelPanel).not.toBeNull();
    const personnelInputs = within(personnelPanel!).getAllByPlaceholderText("多人用顿号或逗号分隔");
    fireEvent.change(personnelInputs[0], { target: { value: "新编辑" } });
    fireEvent.click(within(personnelPanel!).getByRole("button", { name: "保存人员设置" }));

    await waitFor(() => {
      const patchCalls = fetchMock.mock.calls.filter(([, init]) => init?.method === "PATCH");
      expect(patchCalls).toHaveLength(2);
    });
    const patchBodies = fetchMock.mock.calls
      .filter(([, init]) => init?.method === "PATCH")
      .map(([, init]) => JSON.parse(String(init?.body)) as Record<string, unknown>);
    expect(patchBodies[0]).toEqual({ headline: "新的文章标题" });
    expect(patchBodies[1]).toEqual({ editors: ["新编辑"], reviewers: ["责编"], approvers: ["审核"] });
  });

  it("shows match history fallbacks and protects unsaved navigation", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(batch)));
    renderRoute("/previews/1/matches/11", "/previews/:batchId/matches/:gameId", <MatchPage />);
    expect(await screen.findByRole("heading", { name: "环境 vs 探微" })).toBeInTheDocument();
    expect(screen.getByText("2024~2025 · 甲｜八强")).toBeInTheDocument();
    expect(screen.getByText("无")).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("粘贴或填写本场比赛的前瞻正文……"), { target: { value: "尚未保存" } });
    fireEvent.click(screen.getByRole("link", { name: "返回批次" }));
    expect(await screen.findByRole("dialog", { name: "有未保存的正文" })).toBeInTheDocument();
  });

  it("reports an invalid match id reached by a direct URL", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(batch)));
    renderRoute("/previews/1/matches/999", "/previews/:batchId/matches/:gameId", <MatchPage />);
    expect(await screen.findByRole("heading", { name: "比赛不存在" })).toBeInTheDocument();
    expect(screen.getByText(/不属于当前批次/)).toBeInTheDocument();
  });
});

describe("article preview page", () => {
  it("loads the latest stale article and presents a warning", async () => {
    const staleBatch = { ...batch, latest_preview_article_id: 44 };
    const article: Article = {
      id: 44,
      batch_id: 1,
      article_type: "preview",
      version_number: 1,
      title: "历史记录",
      body_html: "<p>历史记录</p>",
      author: "清华绿茵",
      digest: "摘要",
      source_url: "",
      template_version: "v1",
      content_fingerprint: "fingerprint",
      cover_kind: "media_id",
      cover_storage_key: "cover",
      cover_sha256: "sha",
      is_complete: true,
      missing_fields: ["matches.11.writers"],
      input_snapshot: { matches: batch.matches },
      created_at: "2026-08-01T00:00:00Z",
      is_current: null,
    };
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => Promise.resolve(json(String(input).includes("/api/articles/") ? article : staleBatch))));
    renderRoute("/previews/1/article", "/previews/:batchId/article", <PreviewPage />);
    expect(await screen.findByText("当前数据已发生变化，文章已过期。")).toBeInTheDocument();
    expect(screen.getByText("已过期，仅供查看")).toBeInTheDocument();
    expect(screen.getByText("环境 vs 探微 · 作者")).toBeInTheDocument();
    expect(screen.getByTitle("文章预览")).toHaveAttribute("src", "/api/articles/44/preview");
    expect(screen.getByRole("link", { name: "全屏预览" })).toHaveAttribute("href", "/api/articles/44/preview");
    expect(screen.getByRole("link", { name: "全屏预览" })).toHaveAttribute("target", "_blank");
  });
});
