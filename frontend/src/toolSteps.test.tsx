import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import {
  groupSteps,
  stepsFromRun,
  ToolStepCard,
  ToolStepList,
} from "./toolSteps";

const mocks = vi.hoisted(() => ({
  pending: { data: [] as { id: string }[] },
  resolve: { mutate: vi.fn(), isPending: false, error: null as unknown },
}));
vi.mock("./artifact", () => ({
  ArtifactView: ({ title }: { title: string }) => (
    <figure aria-label="artifact">{title}</figure>
  ),
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

test("the user's open/close wins over the waiting state", () => {
  mocks.pending.data = [{ id: "ap1" }];
  const step = {
    id: "c1",
    name: "run_command",
    args: {},
    approvalId: "ap1",
  };
  const { container, rerender } = render(<ToolStepCard step={step} />);
  const details = container.querySelector("details") as HTMLDetailsElement;
  expect(details.open).toBe(true); // waiting: opened for the user
  details.open = false;
  fireEvent(details, new Event("toggle"));
  rerender(<ToolStepCard step={{ ...step }} />);
  expect(details.open).toBe(false); // closed by the user, stays closed
});

test("a call id reused in a later turn pairs with its own result", () => {
  const steps = stepsFromRun([
    row({ seq: 1, kind: "tool_call", call_id: "call_1", tool_name: "a" }),
    row({ seq: 2, kind: "tool_result", call_id: "call_1", output: "first" }),
    row({ seq: 3, kind: "tool_call", call_id: "call_1", tool_name: "b" }),
    row({ seq: 4, kind: "tool_result", call_id: "call_1", output: "second" }),
  ] as never);
  expect(steps.map((s) => [s.name, s.output])).toEqual([
    ["a", "first"],
    ["b", "second"],
  ]);
});

const call = (
  id: string,
  name: string,
  extra: Record<string, unknown> = {},
) => ({
  id,
  name,
  args: { path: id },
  output: "ok",
  ...extra,
});
const ART = call("a1", "render_artifact", {
  args: { title: "차트", kind: "svg", code: "<svg/>" },
});

test("consecutive calls fold into one line and open on click", () => {
  mocks.pending.data = [];
  const { container } = render(
    <ToolStepList
      steps={[
        call("c1", "read_file"),
        call("c2", "grep_files"),
        call("c3", "read_file", { isError: true, output: "nope" }),
      ]}
    />,
  );
  const groups = container.querySelectorAll("details");
  expect(groups).toHaveLength(1);
  const summary = groups[0].querySelector("summary") as HTMLElement;
  expect(summary.textContent).toContain("도구 3개 실행");
  expect(summary.textContent).toContain("read_file, grep_files");
  expect(summary.textContent).toContain("실패 1");
  expect(screen.queryByText("nope")).toBeNull(); // folded
  groups[0].open = true;
  fireEvent(groups[0], new Event("toggle"));
  expect(container.querySelectorAll("details details")).toHaveLength(3);
});

test("a running group shows progress", () => {
  mocks.pending.data = [];
  render(
    <ToolStepList
      steps={[call("c1", "read_file"), { id: "c2", name: "ls", args: {} }]}
    />,
  );
  expect(screen.getByText("실행 중…")).toBeTruthy();
});

test("render_artifact stays inline and splits the runs around it", () => {
  mocks.pending.data = [];
  const steps = [call("c1", "a"), call("c2", "b"), ART, call("c3", "c")];
  expect(groupSteps(steps, () => false).map((i) => i.kind)).toEqual([
    "group",
    "artifact",
    "single",
  ]);
  const { container } = render(<ToolStepList steps={steps} />);
  expect(screen.getByRole("figure", { name: "artifact" }).textContent).toBe(
    "차트",
  );
  expect(container.querySelectorAll(":scope section > details")).toHaveLength(
    2,
  );
});

test("a call waiting for approval stands out, open, with its buttons", () => {
  mocks.pending.data = [{ id: "ap1" }];
  const waitingStep = {
    id: "c3",
    name: "run_command",
    args: { command: "ls" },
    approvalId: "ap1",
  };
  render(
    <ToolStepList
      steps={[call("c1", "read_file"), call("c2", "grep_files"), waitingStep]}
    />,
  );
  expect(screen.getByText("도구 2개 실행")).toBeTruthy();
  expect(screen.getByText("승인 대기")).toBeTruthy();
  expect(screen.getByRole("button", { name: "승인" })).toBeTruthy();
});

test("a single ordinary call is not grouped", () => {
  expect(groupSteps([call("c1", "a")], () => false)).toEqual([
    { kind: "single", index: 0, step: call("c1", "a") },
  ]);
});

test("a group where every call failed says so", () => {
  mocks.pending.data = [];
  const { container, rerender } = render(
    <ToolStepList
      steps={[
        call("c1", "read_file", { isError: true, output: "x" }),
        call("c2", "grep_files", { isError: true, output: "y" }),
      ]}
    />,
  );
  const summary = () =>
    (container.querySelector("summary") as HTMLElement).textContent ?? "";
  expect(summary()).toContain("실패 2");
  expect(summary()).not.toContain("일부 실패");
  expect(summary()).toContain("실패 2실패"); // badge, then the state label
  rerender(
    <ToolStepList
      steps={[
        call("c1", "read_file", { isError: true, output: "x" }),
        call("c2", "grep_files"),
      ]}
    />,
  );
  expect(summary()).toContain("일부 실패");
});
