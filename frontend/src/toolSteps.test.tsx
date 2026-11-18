import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { stepsFromRun, ToolStepCard } from "./toolSteps";

const mocks = vi.hoisted(() => ({
  pending: { data: [] as { id: string }[] },
  resolve: { mutate: vi.fn(), isPending: false, error: null as unknown },
}));
vi.mock("./api", () => ({
  usePendingApprovals: () => mocks.pending,
  useResolveApproval: () => mocks.resolve,
  useRunSteps: () => ({ data: [] }),
}));

afterEach(cleanup);

const row = (fields: Record<string, unknown>) => ({
  id: String(fields.seq),
  call_id: null,
  tool_name: null,
  args_json: null,
  output: null,
  is_error: false,
  approval_id: null,
  created_at: "2026-11-18T00:00:00Z",
  ...fields,
});

test("stored steps pair calls, approvals and results", () => {
  const steps = stepsFromRun([
    row({
      seq: 1,
      kind: "tool_call",
      call_id: "c1",
      tool_name: "rm",
      args_json: { p: "a" },
    }),
    row({
      seq: 2,
      kind: "approval",
      tool_name: "rm",
      approval_id: "ap1",
      output: "파일 삭제: a",
    }),
    row({
      seq: 3,
      kind: "tool_result",
      call_id: "c1",
      tool_name: "rm",
      output: "x",
      is_error: true,
    }),
    row({
      seq: 4,
      kind: "tool_call",
      call_id: "c2",
      tool_name: "ls",
      args_json: {},
    }),
  ] as never);
  expect(steps).toEqual([
    {
      id: "c1",
      name: "rm",
      args: { p: "a" },
      approvalId: "ap1",
      summary: "파일 삭제: a",
      output: "x",
      isError: true,
    },
    { id: "c2", name: "ls", args: {} },
  ]);
});

test("a call waiting for approval shows approve and reject", () => {
  mocks.pending.data = [{ id: "ap1" }];
  render(
    <ToolStepCard
      step={{
        id: "c1",
        name: "run_command",
        args: { command: "ls" },
        approvalId: "ap1",
        summary: "명령 실행: ls",
      }}
    />,
  );
  expect(screen.getByText("승인 대기")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "승인" }));
  expect(mocks.resolve.mutate).toHaveBeenCalledWith({
    id: "ap1",
    approve: true,
  });
  fireEvent.click(screen.getByRole("button", { name: "거절" }));
  expect(mocks.resolve.mutate).toHaveBeenCalledWith({
    id: "ap1",
    approve: false,
  });
});

test("a finished call shows its output, no buttons", () => {
  mocks.pending.data = [];
  render(
    <ToolStepCard
      step={{
        id: "c1",
        name: "read_file",
        args: { path: "a" },
        output: "hello",
      }}
    />,
  );
  expect(screen.getByText("완료")).toBeTruthy();
  expect(screen.getByText("hello")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "승인" })).toBeNull();
});
