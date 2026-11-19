import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { ArtifactView } from "./artifact";
import { artifactOf } from "./toolSteps";

afterEach(cleanup);

test("html runs sandboxed: scripts only, never same-origin, no network", async () => {
  render(
    <ArtifactView
      title="Demo"
      kind="html"
      code="<h1>Hi</h1><script>1</script>"
    />,
  );
  const frame = await waitFor(
    () => screen.getByTitle("Demo") as HTMLIFrameElement,
  );
  expect(frame.getAttribute("sandbox")).toBe("allow-scripts");
  const doc = frame.getAttribute("srcdoc") ?? "";
  expect(doc).toContain("default-src 'none'");
  expect(doc).toContain("connect-src 'none'");
  expect(doc).toContain("<h1>Hi</h1>");
});

test("svg gets no scripts at all", async () => {
  render(
    <ArtifactView
      title="Pic"
      kind="svg"
      code="<svg><script>alert(1)</script></svg>"
    />,
  );
  const frame = await waitFor(
    () => screen.getByTitle("Pic") as HTMLIFrameElement,
  );
  expect(frame.getAttribute("sandbox")).toBe("");
  expect(frame.getAttribute("srcdoc")).not.toContain("script-src");
});

test("tsx is transpiled onto an inlined Preact", async () => {
  const code =
    "import { useState } from 'react';\nexport default function App() { const [n] = useState(2); return <b>{n}</b>; }";
  render(<ArtifactView title="Counter" kind="tsx" code={code} />);
  const frame = await waitFor(
    () => screen.getByTitle("Counter") as HTMLIFrameElement,
    {
      timeout: 4000,
    },
  );
  const doc = frame.getAttribute("srcdoc") ?? "";
  expect(frame.getAttribute("sandbox")).toBe("allow-scripts");
  expect(doc).toContain("preactHooks");
  expect(doc).toContain('require("react")'.replace(/"/g, "'"));
});

test("only finished render_artifact calls become artifacts", () => {
  const step = {
    id: "c1",
    name: "render_artifact",
    args: { title: "T", kind: "svg", code: "<svg/>" },
  };
  expect(artifactOf(step)).toBeNull(); // still running
  expect(artifactOf({ ...step, output: "{}" })).toEqual({
    title: "T",
    kind: "svg",
    code: "<svg/>",
  });
  expect(artifactOf({ ...step, output: "x", isError: true })).toBeNull();
  expect(
    artifactOf({ ...step, output: "{}", args: { ...step.args, kind: "pdf" } }),
  ).toBeNull();
});
