import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { SetupTool } from "../api";
import { ToolRow } from "./ToolIntegrations";

const { mocks } = vi.hoisted(() => ({
  mocks: {
    connect: { mutate: vi.fn(), isPending: false, error: null, variables: {} },
    disconnect: {
      mutate: vi.fn(),
      isPending: false,
      error: null,
      variables: {},
    },
  },
}));
vi.mock("../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api")>()),
  useConnectTool: () => mocks.connect,
  useDisconnectTool: () => mocks.disconnect,
}));

function tool(over: Partial<SetupTool> = {}): SetupTool {
  return {
    name: "claude",
    label: "Claude Code",
    installed: true,
    path: "/bin/claude",
    version: "claude 9.9.9",
    connectable: true,
    connected: false,
    skill: false,
    skill_supported: true,
    skill_path: "/home/.claude/skills/argos",
    skill_conflict: false,
    hint: "",
    shared_with: null,
    mcp_connected: false,
    skill_installed: false,
    ...over,
  };
}

afterEach(cleanup);
beforeEach(() => {
  localStorage.setItem("argos-language", "ko");
  mocks.connect.mutate.mockReset();
  mocks.disconnect.mutate.mockReset();
});

test("MCP and skill connect on their own, or both at once", () => {
  render(<ToolRow tool={tool()} />);
  fireEvent.click(screen.getByRole("button", { name: "MCP 연결" }));
  expect(mocks.connect.mutate).toHaveBeenLastCalledWith({
    name: "claude",
    mcp: true,
    skill: false,
  });
  fireEvent.click(screen.getByRole("button", { name: "스킬 설치" }));
  expect(mocks.connect.mutate).toHaveBeenLastCalledWith({
    name: "claude",
    mcp: false,
    skill: true,
  });
  fireEvent.click(screen.getByRole("button", { name: "Argos에 연결" }));
  expect(mocks.connect.mutate).toHaveBeenLastCalledWith({
    name: "claude",
    mcp: true,
    skill: true,
  });
});

test("removing only the skill asks first, then keeps MCP", () => {
  render(<ToolRow tool={tool({ connected: true, skill: true })} />);
  expect(screen.getByText("/home/.claude/skills/argos")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "스킬 제거" }));
  expect(mocks.disconnect.mutate).not.toHaveBeenCalled();
  expect(screen.getByText(/Claude Code에서 Argos 스킬을/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "제거" }));
  expect(mocks.disconnect.mutate).toHaveBeenCalledWith({
    name: "claude",
    mcp: false,
    skill: true,
  });
});

test("connecting the rest asks only for the missing part", () => {
  render(<ToolRow tool={tool({ connected: true })} />);
  expect(screen.getByRole("button", { name: "MCP 해제" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "나머지 연결" }));
  expect(mocks.connect.mutate).toHaveBeenCalledWith({
    name: "claude",
    mcp: false,
    skill: true,
  });
  cleanup();
  render(<ToolRow tool={tool({ skill: true })} />);
  fireEvent.click(screen.getByRole("button", { name: "나머지 연결" }));
  expect(mocks.connect.mutate).toHaveBeenLastCalledWith({
    name: "claude",
    mcp: true,
    skill: false,
  });
});

test("a tool without skills is MCP only", () => {
  render(
    <ToolRow
      tool={tool({ name: "x", skill_supported: false, skill_path: null })}
    />,
  );
  expect(screen.queryByRole("button", { name: /스킬/ })).toBeNull();
  expect(screen.getByText(/MCP만 연결해요/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Argos에 연결" }));
  expect(mocks.connect.mutate).toHaveBeenCalledWith({
    name: "x",
    mcp: true,
    skill: false,
  });
});

test("the IDE says when it shares the CLI's config", () => {
  render(
    <ToolRow
      tool={tool({
        name: "antigravity_ide",
        label: "Antigravity IDE",
        version: null,
        shared_with: "antigravity",
      })}
    />,
  );
  expect(
    screen.getByText(/mcp_config.json에 argos 항목만/),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/Antigravity CLI와 같은 설정 파일/),
  ).toBeInTheDocument();
});
