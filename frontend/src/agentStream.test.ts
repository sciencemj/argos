import { renderHook } from "@testing-library/react";
import { expect, test } from "vitest";
import { onAgentEvent, useLive } from "./agentStream";

test("a reused call id gives the result to the newest open call only", () => {
  const ids = { message_id: "m1", run_id: "r1", agent_id: "a" };
  onAgentEvent("agent.tool_call", {
    ...ids,
    id: "call_1",
    name: "a",
    args: {},
  });
  onAgentEvent("agent.tool_result", { ...ids, id: "call_1", output: "first" });
  onAgentEvent("agent.tool_call", {
    ...ids,
    id: "call_1",
    name: "b",
    args: {},
  });
  onAgentEvent("agent.tool_result", {
    ...ids,
    id: "call_1",
    output: "second",
    is_error: true,
  });
  const { result } = renderHook(() => useLive("m1"));
  expect(
    result.current?.steps.map((s) => [s.name, s.output, s.isError]),
  ).toEqual([
    ["a", "first", false],
    ["b", "second", true],
  ]);
  onAgentEvent("agent.done", ids);
});
