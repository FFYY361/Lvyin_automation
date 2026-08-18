import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { AuthProvider } from "../auth";
import { TutorialPage } from "../pages/TutorialPage";
import { WechatDraftPage } from "../pages/WechatDraftPage";
import type { User } from "../types";

const admin: User = { id: 1, username: "admin", display_name: "管理员", role: "admin", is_active: true };
const member: User = { id: 2, username: "writer", display_name: "撰稿人", role: "user", is_active: true };

function json(value: unknown) {
  return new Response(JSON.stringify(value), { status: 200, headers: { "Content-Type": "application/json" } });
}

function renderPage(element: React.ReactNode, user: User, path: string) {
  const router = createMemoryRouter([{ path, element }], { initialEntries: [path] });
  render(<AuthProvider initialUser={user}><RouterProvider router={router} /></AuthProvider>);
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("role-aware tutorial", () => {
  it("shows both role tutorials to administrators", () => {
    renderPage(<TutorialPage />, admin, "/tutorial");
    expect(screen.getByText("管理员工作流")).toBeInTheDocument();
    expect(screen.getAllByRole("img")).toHaveLength(12);
    expect(screen.getAllByRole("figure")).toHaveLength(12);
    for (const item of screen.getAllByRole("figure")) {
      expect(item.querySelector("img")?.getAttribute("alt")).toBeTruthy();
      expect(item.querySelectorAll(".tutorial-figure__marker").length).toBeGreaterThan(0);
      expect(item.querySelectorAll(".tutorial-figure__marker").length).toBeLessThanOrEqual(3);
    }
    expect(screen.getByText(/缺项页面故意保留未填写内容用于教学/)).toBeInTheDocument();
    expect(screen.getByText(/有缺项的批次仍然可以渲染文章并预览/)).toBeInTheDocument();
    expect(screen.getByText(/只有已完善的批次/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "普通用户教程" }));
    expect(screen.getByText("普通用户工作流")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "额外功能" })).toBeInTheDocument();
    expect(screen.getByText("7 个步骤")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "进入已领取的比赛" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "查看前瞻文章" })).toBeInTheDocument();
    expect(screen.queryByText(/只有已完善的批次/)).not.toBeInTheDocument();
    expect(screen.getAllByRole("img")).toHaveLength(8);
  });

  it("never renders administrator tutorial controls for normal users", () => {
    renderPage(<TutorialPage />, member, "/tutorial");
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(screen.queryByText("管理员工作流")).not.toBeInTheDocument();
    expect(screen.getByText("普通用户工作流")).toBeInTheDocument();
    expect(screen.getByText("7 个步骤")).toBeInTheDocument();
    expect(screen.getByText(/关闭只禁止继续领取，不会清空内容/)).toBeInTheDocument();
    expect(screen.getByText(/按钮变回不可用且页面显示“已保存”后/)).toBeInTheDocument();
    expect(screen.getByText(/可以查看批次内的比赛，并打开管理员最近一次渲染的前瞻文章/)).toBeInTheDocument();
    expect(screen.queryByText(/存在缺项|只有已完善|微信公众号草稿箱|文章会过期/)).not.toBeInTheDocument();
    expect(screen.queryByText(/4 月 18|4 月 19/)).not.toBeInTheDocument();
    expect(document.querySelectorAll(".tutorial-figure__marker--badge-right")).toHaveLength(2);
    expect(screen.getAllByRole("img")).toHaveLength(8);
  });
});

describe("wechat draft task-closing notice", () => {
  const candidates = [
    { batch: { id: 1, batch_date: "2026-08-08", competition: "male" }, article: { id: 11, article_type: "preview", version_number: 1, title: "周末前瞻" } },
    { batch: { id: 2, batch_date: "2026-08-09", competition: "female" }, article: { id: 12, article_type: "report", version_number: 1, title: "周末战报" } },
  ];

  it("warns only when the selection contains a preview article", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      if (!init?.method) return Promise.resolve(json({ items: candidates }));
      return Promise.resolve(json({ status: "ready", publication_fingerprint: "fingerprint", articles: [] }));
    }));
    renderPage(<WechatDraftPage />, admin, "/wechat-drafts");
    fireEvent.click(await screen.findByRole("checkbox", { name: /周末前瞻/ }));
    fireEvent.click(screen.getByRole("button", { name: "核对并继续" }));
    expect(await screen.findByText(/创建成功后，所选前瞻文章对应批次的任务将自动关闭/)).toBeInTheDocument();
    cleanup();

    renderPage(<WechatDraftPage />, admin, "/wechat-drafts");
    fireEvent.click(await screen.findByRole("checkbox", { name: /周末战报/ }));
    fireEvent.click(screen.getByRole("button", { name: "核对并继续" }));
    expect(await screen.findByRole("dialog", { name: "确认创建微信草稿" })).toBeInTheDocument();
    expect(screen.queryByText(/任务将自动关闭/)).not.toBeInTheDocument();
  });
});
