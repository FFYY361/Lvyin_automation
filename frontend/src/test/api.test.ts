import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api } from "../api";

afterEach(() => vi.unstubAllGlobals());

describe("api client", () => {
  it("normalizes workflow errors and preserves details", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: "body_version_conflict", message: "updated", details: { body_version: 2 } } }), { status: 409, headers: { "Content-Type": "application/json" } })));
    await expect(api("/api/test")).rejects.toMatchObject({ status: 409, code: "body_version_conflict", message: "updated", details: { body_version: 2 } } satisfies Partial<ApiError>);
  });

  it("sends cookies and JSON content type", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    await api("/api/test", { method: "POST", body: JSON.stringify({ value: 1 }) });
    expect(fetchMock).toHaveBeenCalledWith("/api/test", expect.objectContaining({ credentials: "include", method: "POST" }));
    const headers = fetchMock.mock.calls[0][1].headers as Headers;
    expect(headers.get("Content-Type")).toBe("application/json");
  });
});
