import { render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import App from "./App";

afterEach(() => {
  vi.unstubAllGlobals();
});

test("shows backend health", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValue(
        new Response(
          JSON.stringify({ status: "ok", db: "ok", journal_mode: "wal" }),
        ),
      ),
  );
  render(<App />);
  expect(
    await screen.findByText(/서버 연결됨 · DB ok \(wal\)/),
  ).toBeInTheDocument();
});

test("shows error when backend is down", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response("", { status: 502 })),
  );
  render(<App />);
  expect(await screen.findByRole("alert")).toHaveTextContent("HTTP 502");
});
