import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { ContextRing, contextUsage } from "./composerBar";

afterEach(cleanup);

test("context usage uses the backend's window", () => {
  const small = contextUsage(0, "native");
  expect(small.window).toBe(32_000);
  expect(small.tokens).toBe(3000); // the system share alone
  expect(contextUsage(0, "claude_code").window).toBe(200_000);
  expect(contextUsage(1e9, "native").ratio).toBe(1);
});

test("the ring warns near the limit", () => {
  render(<ContextRing chars={80_000} backend="native" />);
  const ring = screen.getByRole("img");
  expect(ring.getAttribute("title")).toMatch(/tokens · \d+%/);
  expect(ring.className).toContain("text-warn");
});
