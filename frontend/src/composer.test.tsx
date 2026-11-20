import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { ComposerHighlight, chatAgentName } from "./feed";

afterEach(cleanup);

test("colors known agent mentions and Argos slash commands", () => {
  render(
    <ComposerHighlight
      text="/task 과제 마감 @claude"
      agents={[{ name: "claude" }]}
      skills={[]}
    />,
  );
  expect(screen.getByText("/task")).toHaveClass("text-warn");
  expect(screen.getByText("@claude")).toHaveStyle({
    color: "var(--claude-text)",
  });
  expect(screen.getByText(/과제 마감/)).toBeTruthy();
});

test("does not color unknown agents or slash words inside ordinary text", () => {
  render(
    <ComposerHighlight
      text="그냥 /task 얘기 @unknown"
      agents={[{ name: "claude" }]}
      skills={[]}
    />,
  );
  expect(screen.queryByText("/task")).toBeNull();
  expect(screen.queryByText("@unknown")).toBeNull();
  expect(screen.getByText("그냥 /task 얘기 @unknown")).toBeTruthy();
});

test("colors a known agent skill command", () => {
  render(
    <ComposerHighlight
      text="/review 코드 확인"
      agents={[]}
      skills={[{ name: "review" }]}
    />,
  );
  expect(screen.getByText("/review")).toHaveClass("text-warn");
});

const base = {
  picked: null,
  chatBar: true,
  coding: false,
  hasDefaultAssistant: true,
  channelAgent: "hermes",
};

test("a new chat goes to the default assistant, no agent needed", () => {
  expect(chatAgentName(base)).toBe("argos");
});

test("a picked agent or the thread's own agent wins", () => {
  expect(chatAgentName({ ...base, picked: "claude" })).toBe("claude");
  expect(chatAgentName({ ...base, sticky: "codex" })).toBe("codex");
});

test("coding mode and the feed keep the channel's agent", () => {
  expect(chatAgentName({ ...base, coding: true })).toBe("hermes");
  expect(chatAgentName({ ...base, chatBar: false })).toBe("hermes");
  expect(chatAgentName({ ...base, hasDefaultAssistant: false })).toBe("hermes");
});
