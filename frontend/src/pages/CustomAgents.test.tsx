import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, expect, test, vi } from "vitest";
import { selectValue } from "../test-select";
import { AgentsSection, windowName } from "./CustomAgents";

const mocks = vi.hoisted(() => ({
  save: { mutate: vi.fn(), isPending: false, error: null as unknown },
  modelsFor: vi.fn(),
  agents: [] as Record<string, unknown>[],
  saveDefault: vi.fn(),
  setModel: vi.fn(),
  remove: vi.fn(),
}));
vi.mock("../api", () => ({
  agentExportUrl: (name: string) => `/export/${name}`,
  useAgents: () => ({ data: mocks.agents }),
  useAgentTools: () => ({ data: [] }),
  useChannels: () => ({ data: { channels: [] } }),
  useDeleteAgent: () => ({
    mutate: mocks.remove,
    isPending: false,
    error: null,
  }),
  useAgentSettings: () => ({ data: { default_agent: "claude" } }),
  useSaveDefaultAgent: () => ({
    mutate: mocks.saveDefault,
    isPending: false,
    error: null,
  }),
  useSetAgentModel: () => ({
    mutate: mocks.setModel,
    isPending: false,
    error: null,
  }),
  useClassifierSettings: () => ({ data: { provider_id: "home" } }),
  useUsage: () => ({
    data: {
      providers: [
        {
          provider: "claude",
          state: "ok",
          message: "",
          windows: [
            { name: "5h", used_percent: 72.4 },
            { name: "10080m", used_percent: 11 },
          ],
        },
      ],
    },
  }),
  useImportAgent: () => ({ mutate: vi.fn(), isPending: false, error: null }),
  useSaveAgent: () => mocks.save,
  useProviders: () => ({
    data: [
      {
        id: "home",
        name: "Home",
        kind: "ollama",
        base_url: "http://127.0.0.1:11434/v1",
        has_api_key: false,
        strict_locality: null,
        locality: "device",
        is_default: true,
      },
      {
        id: "gpu",
        name: "GPU",
        kind: "ollama",
        base_url: "http://192.0.2.10:11434/v1",
        has_api_key: false,
        strict_locality: null,
        locality: "network",
        is_default: false,
      },
      {
        id: "lm",
        name: "LM",
        kind: "openai",
        base_url: "http://192.0.2.11:1234/v1",
        has_api_key: false,
        strict_locality: null,
        locality: "network",
        is_default: false,
      },
    ],
  }),
  useAgentModels: (backend: string, providerId?: string | null) => {
    mocks.modelsFor(backend, providerId);
    return {
      data: {
        models: [
          {
            id: "gemma4:e4b",
            label: "gemma4:e4b",
            description: "",
            default: false,
          },
        ],
      },
    };
  },
}));

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.open = true;
  };
  HTMLDialogElement.prototype.close = function close() {
    this.open = false;
  };
});
beforeEach(() => {
  localStorage.setItem("argos-language", "ko");
  mocks.save.mutate.mockReset();
  mocks.modelsFor.mockReset();
  mocks.agents = [];
  mocks.saveDefault.mockReset();
  mocks.setModel.mockReset();
  mocks.remove.mockReset();
});
afterEach(cleanup);

function openNewAgent(provider: string) {
  render(<AgentsSection />);
  fireEvent.click(screen.getByRole("button", { name: "봇 만들기" }));
  fireEvent.change(screen.getByLabelText("@이름"), {
    target: { value: "gemma" },
  });
  fireEvent.change(screen.getByLabelText("표시 이름"), {
    target: { value: "Gemma" },
  });
  selectValue(screen.getByLabelText("기반 에이전트"), "ollama");
  selectValue(screen.getByLabelText("모델 연결"), provider);
}

test("picks provider then model", () => {
  openNewAgent("gpu");
  expect(mocks.modelsFor).toHaveBeenLastCalledWith("ollama", "gpu");
  selectValue(screen.getByLabelText("모델"), "gemma4:e4b");
  fireEvent.click(screen.getByRole("button", { name: "만들기" }));
  expect(mocks.save.mutate).toHaveBeenCalledWith(
    expect.objectContaining({
      backend: "ollama",
      provider_id: "gpu",
      model: "gemma4:e4b",
      send_images: false,
    }),
    expect.anything(),
  );
});

test("send images toggle only for openai providers", () => {
  openNewAgent("gpu");
  expect(screen.queryByLabelText("이미지 보내기")).toBeNull();
  selectValue(screen.getByLabelText("모델 연결"), "lm");
  fireEvent.click(screen.getByLabelText("이미지 보내기"));
  fireEvent.click(screen.getByRole("button", { name: "만들기" }));
  expect(mocks.save.mutate).toHaveBeenCalledWith(
    expect.objectContaining({ provider_id: "lm", send_images: true }),
    expect.anything(),
  );
});

test("changing provider clears model", () => {
  openNewAgent("gpu");
  selectValue(screen.getByLabelText("모델"), "gemma4:e4b");
  selectValue(screen.getByLabelText("모델 연결"), "home");
  expect(screen.getByLabelText("모델").dataset.value).toBe("");
});

test("default provider is sent as null", () => {
  openNewAgent("home");
  fireEvent.click(screen.getByRole("button", { name: "만들기" }));
  expect(mocks.save.mutate).toHaveBeenCalledWith(
    expect.objectContaining({ provider_id: null }),
    expect.anything(),
  );
});

test("renames an existing agent", () => {
  mocks.agents = [
    {
      id: "a1",
      name: "gemma",
      display_name: "Gemma",
      avatar: null,
      backend: "ollama",
      model: "gemma4:e4b",
      is_builtin: false,
      system_prompt: null,
      tools: [],
      channel_ids: [],
      available: true,
      problem: null,
      provider_id: "gpu",
      send_images: false,
    },
  ];
  render(<AgentsSection />);
  fireEvent.click(screen.getByRole("button", { name: "수정" }));
  const handle = screen.getByLabelText("@이름") as HTMLInputElement;
  expect(handle.disabled).toBe(false);
  fireEvent.change(handle, { target: { value: "Gem2" } });
  fireEvent.click(screen.getByRole("button", { name: "저장" }));
  expect(mocks.save.mutate).toHaveBeenCalledWith(
    expect.objectContaining({ editing: "gemma", name: "gem2" }),
    expect.anything(),
  );
});

test("unchanged handle is not sent when editing", () => {
  mocks.agents = [
    {
      id: "a1",
      name: "gemma",
      display_name: "Gemma",
      avatar: null,
      backend: "claude_code",
      model: null,
      is_builtin: false,
      system_prompt: null,
      tools: [],
      channel_ids: [],
      available: true,
      problem: null,
      provider_id: null,
      send_images: false,
    },
  ];
  render(<AgentsSection />);
  fireEvent.click(screen.getByRole("button", { name: "수정" }));
  fireEvent.click(screen.getByRole("button", { name: "저장" }));
  expect(mocks.save.mutate).toHaveBeenCalledWith(
    expect.objectContaining({ editing: "gemma", name: undefined }),
    expect.anything(),
  );
});

const BUILTIN = {
  id: "b1",
  name: "claude",
  display_name: "Claude",
  avatar: null,
  backend: "claude_code",
  model: null,
  is_builtin: true,
  system_prompt: null,
  tools: null,
  channel_ids: [],
  available: true,
  problem: null,
  provider_id: null,
  send_images: false,
};

test("one list: built-in with model, usage and default; custom with actions", () => {
  mocks.agents = [
    BUILTIN,
    {
      ...BUILTIN,
      id: "a1",
      name: "gemma",
      display_name: "Gemma",
      backend: "ollama",
      is_builtin: false,
      tools: [],
      provider_id: "gpu",
      model: "gemma4:e4b",
    },
  ];
  render(<AgentsSection />);
  // Built-in: usage summary, model picker, and it is the default
  expect(screen.getByText(/5h 72% · 7d 11%/)).toBeTruthy();
  selectValue(screen.getByLabelText("Claude 모델"), "gemma4:e4b");
  expect(mocks.setModel).toHaveBeenCalledWith({
    name: "claude",
    model: "gemma4:e4b",
  });
  expect(screen.getByText("기본")).toBeTruthy();
  // Custom: make it the default right from its row
  fireEvent.click(
    screen.getByRole("button", { name: "@gemma을(를) 기본 에이전트로" }),
  );
  expect(mocks.saveDefault).toHaveBeenCalledWith("gemma");
  expect(screen.getByText(/GPU · gemma4:e4b/)).toBeTruthy();
});

test("deleting a bot asks in a dialog first", () => {
  mocks.agents = [
    { ...BUILTIN, id: "a1", name: "gemma", is_builtin: false, tools: [] },
  ];
  render(<AgentsSection />);
  fireEvent.click(screen.getByRole("button", { name: "삭제" }));
  expect(mocks.remove).not.toHaveBeenCalled();
  const dialog = screen.getByRole("dialog");
  fireEvent.click(
    [...dialog.querySelectorAll("button")].find(
      (b) => b.textContent === "삭제",
    ) as HTMLButtonElement,
  );
  expect(mocks.remove).toHaveBeenCalledWith("gemma");
});

test("usage windows in minutes read as hours or days", () => {
  expect(windowName("300m")).toBe("5h");
  expect(windowName("43200m")).toBe("30d");
  expect(windowName("10080m")).toBe("7d");
  expect(windowName("90m")).toBe("90m");
  expect(windowName("5h")).toBe("5h");
});

test("the default assistant picks its own model connection", () => {
  mocks.agents = [
    {
      ...BUILTIN,
      id: "d1",
      name: "argos",
      display_name: "Argos",
      backend: "native",
      provider_id: null,
    },
  ];
  render(<AgentsSection />);
  expect(screen.getByText(/메인 기억/)).toBeTruthy();
  selectValue(screen.getByLabelText("Argos 모델 연결"), "gpu");
  expect(mocks.setModel).toHaveBeenCalledWith({
    name: "argos",
    model: null,
    provider_id: "gpu",
  });
  expect(mocks.modelsFor).toHaveBeenCalledWith("native", undefined);
});
