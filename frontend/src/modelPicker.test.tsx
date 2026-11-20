import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { agentInfo } from "./agents";
import { filterModels, ModelPicker } from "./composerBar";

const mocks = vi.hoisted(() => ({
  save: vi.fn(),
  chat: {
    provider_id: null,
    model: null,
    effort: "medium",
    provider_name: "Home",
    resolved_model: "qwen3.5:9b",
    problem: null as string | null,
  },
}));
const MODELS = [
  {
    provider_id: "home",
    provider_name: "Home",
    model: "qwen3.5:9b",
    remote: false,
  },
  {
    provider_id: "home",
    provider_name: "Home",
    model: "gemma4:e4b",
    remote: false,
  },
  { provider_id: "gpu", provider_name: "GPU", model: "llama3", remote: false },
];
vi.mock("./api", () => ({
  useChatSettings: () => ({ data: mocks.chat }),
  useSaveChatSettings: () => ({ mutate: mocks.save, isPending: false }),
  useChatModels: () => ({
    data: { models: MODELS, errors: [] },
    isLoading: false,
  }),
  useUpdateChannel: () => ({ mutate: vi.fn() }),
}));

beforeEach(() => {
  localStorage.setItem("argos-language", "ko");
  mocks.save.mockReset();
  mocks.chat.problem = null;
});
afterEach(cleanup);

test("shows the current model and effort, searchable across connections", () => {
  render(<ModelPicker />);
  const button = screen.getByRole("button", { name: "채팅 모델" });
  expect(button.textContent).toContain("qwen3.5:9b");
  expect(button.textContent).toContain("중간");
  fireEvent.click(button);
  expect(screen.getAllByRole("option")).toHaveLength(3);
  fireEvent.change(screen.getByLabelText("모델 검색"), {
    target: { value: "gpu" },
  });
  const [only] = screen.getAllByRole("option");
  expect(only.textContent).toContain("llama3");
  fireEvent.click(only);
  expect(mocks.save).toHaveBeenCalledWith({
    provider_id: "gpu",
    model: "llama3",
  });
});

test("reasoning effort is picked below the list", () => {
  render(<ModelPicker />);
  fireEvent.click(screen.getByRole("button", { name: "채팅 모델" }));
  expect(screen.getByRole("option", { selected: true }).textContent).toContain(
    "qwen3.5:9b",
  );
  fireEvent.click(screen.getByRole("button", { name: "높음" }));
  expect(mocks.save).toHaveBeenCalledWith({ effort: "high" });
});

test("nothing to run on: says so", () => {
  mocks.chat.problem = "기본 채팅 모델이 정해지지 않았어요";
  render(<ModelPicker />);
  fireEvent.click(screen.getByRole("button", { name: "채팅 모델" }));
  expect(screen.getByText("기본 채팅 모델이 정해지지 않았어요")).toBeTruthy();
});

test("filterModels matches model or connection name", () => {
  expect(filterModels(MODELS, "GEMMA").map((m) => m.model)).toEqual([
    "gemma4:e4b",
  ]);
  expect(filterModels(MODELS, " ")).toHaveLength(3);
});

test("default chat is labelled as such, never as an agent called argos", () => {
  localStorage.setItem("argos-language", "ko");
  expect(agentInfo("_chat").name).toBe("기본 채팅");
  expect(agentInfo("_chat").initials).not.toBe("_C");
});

test("arrow keys move the highlight, Enter picks it", () => {
  render(<ModelPicker />);
  fireEvent.click(screen.getByRole("button", { name: "채팅 모델" }));
  const search = screen.getByRole("combobox", { name: "모델 검색" });
  const active = () =>
    document.getElementById(search.getAttribute("aria-activedescendant") ?? "");
  expect(active()?.textContent).toContain("qwen3.5:9b"); // starts on the current one
  fireEvent.keyDown(search, { key: "ArrowDown" });
  expect(active()?.textContent).toContain("gemma4:e4b");
  expect(active()?.dataset.active).toBe("true");
  fireEvent.keyDown(search, { key: "ArrowUp" });
  fireEvent.keyDown(search, { key: "ArrowUp" }); // wraps to the last
  expect(active()?.textContent).toContain("llama3");
  fireEvent.keyDown(search, { key: "Enter" });
  expect(mocks.save).toHaveBeenCalledWith({
    provider_id: "gpu",
    model: "llama3",
  });
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("typing restarts the highlight at the first match; Escape closes", () => {
  render(<ModelPicker />);
  fireEvent.click(screen.getByRole("button", { name: "채팅 모델" }));
  const search = screen.getByRole("combobox", { name: "모델 검색" });
  fireEvent.change(search, { target: { value: "e" } });
  const id = search.getAttribute("aria-activedescendant") ?? "";
  expect(document.getElementById(id)?.textContent).toContain("qwen3.5:9b");
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("dialog")).toBeNull();
});
