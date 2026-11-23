import { QueryClient } from "@tanstack/react-query";
import { expect, test, vi } from "vitest";
import { afterToolWrite, type Setup, type SetupTool } from "./api";

function tool(over: Partial<SetupTool>): SetupTool {
  return {
    name: "claude",
    label: "",
    installed: true,
    path: null,
    version: null,
    connectable: true,
    connected: false,
    skill: false,
    skill_supported: true,
    skill_path: null,
    skill_conflict: false,
    hint: "",
    shared_with: null,
    mcp_connected: false,
    skill_installed: false,
    ...over,
  };
}

function cache(tools: SetupTool[]) {
  const qc = new QueryClient();
  qc.setQueryData<Setup>(["setup"], {
    done: false,
    desktop: false,
    data_dir: "",
    tools,
  });
  const invalidate = vi.spyOn(qc, "invalidateQueries");
  const setupRefetched = () =>
    invalidate.mock.calls.some(
      ([filters]) => JSON.stringify(filters?.queryKey) === '["setup"]',
    );
  return { qc, setupRefetched };
}

const cli = tool({ name: "antigravity" });
const ide = tool({ name: "antigravity_ide", shared_with: "antigravity" });

test("a plain tool updates its own row only", () => {
  const { qc, setupRefetched } = cache([tool({}), cli]);
  afterToolWrite(qc, tool({ connected: true }));
  const rows = qc.getQueryData<Setup>(["setup"])?.tools ?? [];
  expect(rows[0].connected).toBe(true);
  expect(setupRefetched()).toBe(false);
});

test("connecting the IDE re-reads the list so the shared CLI row updates", () => {
  const { qc, setupRefetched } = cache([cli, ide]);
  afterToolWrite(qc, { ...ide, connected: true });
  expect(qc.getQueryData<Setup>(["setup"])?.tools[1].connected).toBe(true);
  expect(setupRefetched()).toBe(true);
});

test("connecting the CLI re-reads the list when the IDE shares its config", () => {
  const { qc, setupRefetched } = cache([cli, ide]);
  afterToolWrite(qc, { ...cli, connected: true });
  expect(setupRefetched()).toBe(true);
});
