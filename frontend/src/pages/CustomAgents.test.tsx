import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, expect, test, vi } from "vitest";
import { CustomAgentsSection } from "./CustomAgents";

const mocks = vi.hoisted(() => ({
  save: { mutate: vi.fn(), isPending: false, error: null as unknown },
  modelsFor: vi.fn(),
}));
vi.mock("../api", () => ({
  agentExportUrl: (name: string) => `/export/${name}`,
  useAgents: () => ({ data: [] }),
  useAgentTools: () => ({ data: [] }),
  useChannels: () => ({ data: { channels: [] } }),
  useDeleteAgent: () => ({ mutate: vi.fn(), isPending: false, error: null }),
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
});
afterEach(cleanup);

function openNewAgent(provider: string) {
  render(<CustomAgentsSection />);
  fireEvent.click(screen.getByRole("button", { name: "봇 만들기" }));
  fireEvent.change(screen.getByLabelText("@이름"), {
    target: { value: "gemma" },
  });
  fireEvent.change(screen.getByLabelText("표시 이름"), {
    target: { value: "Gemma" },
  });
  fireEvent.change(screen.getByLabelText("기반 에이전트"), {
    target: { value: "ollama" },
  });
  fireEvent.change(screen.getByLabelText("모델 연결"), {
    target: { value: provider },
  });
}

test("picks provider then model", () => {
  openNewAgent("gpu");
  expect(mocks.modelsFor).toHaveBeenLastCalledWith("ollama", "gpu");
  fireEvent.change(screen.getByLabelText("모델"), {
    target: { value: "gemma4:e4b" },
  });
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
  fireEvent.change(screen.getByLabelText("모델 연결"), {
    target: { value: "lm" },
  });
  fireEvent.click(screen.getByLabelText("이미지 보내기"));
  fireEvent.click(screen.getByRole("button", { name: "만들기" }));
  expect(mocks.save.mutate).toHaveBeenCalledWith(
    expect.objectContaining({ provider_id: "lm", send_images: true }),
    expect.anything(),
  );
});

test("changing provider clears model", () => {
  openNewAgent("gpu");
  fireEvent.change(screen.getByLabelText("모델"), {
    target: { value: "gemma4:e4b" },
  });
  fireEvent.change(screen.getByLabelText("모델 연결"), {
    target: { value: "home" },
  });
  expect((screen.getByLabelText("모델") as HTMLSelectElement).value).toBe("");
});

test("default provider is sent as null", () => {
  openNewAgent("home");
  fireEvent.click(screen.getByRole("button", { name: "만들기" }));
  expect(mocks.save.mutate).toHaveBeenCalledWith(
    expect.objectContaining({ provider_id: null }),
    expect.anything(),
  );
});
