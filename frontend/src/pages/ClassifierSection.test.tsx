import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ClassifierSection } from "./SettingsPage";

const mocks = vi.hoisted(() => ({
  save: {
    mutate: vi.fn(),
    isPending: false,
    isSuccess: false,
    error: null as unknown,
  },
  provider: "ollama" as "ollama" | "hermes",
  modelsFor: vi.fn(),
}));
vi.mock("../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api")>()),
  useClassifierSettings: () => ({
    data: {
      provider: mocks.provider,
      model: "small:1b",
      source: "app",
      enabled: true,
      title_model: "",
      title_mode: "parallel",
      provider_id: "home",
    },
  }),
  useOllamaModels: (providerId?: string) => {
    mocks.modelsFor(providerId);
    return {
      data: {
        reachable: true,
        models: [
          {
            name: providerId === "gpu" ? "gemma4:e4b" : "small:1b",
            remote: false,
            parameter_size: null,
            decision: false,
          },
        ],
      },
      refetch: vi.fn(),
    };
  },
  useSaveClassifier: () => mocks.save,
  useProviders: () => ({
    data: [
      {
        id: "home",
        name: "Home",
        kind: "ollama",
        locality: "device",
        is_default: true,
      },
      {
        id: "gpu",
        name: "GPU",
        kind: "ollama",
        locality: "network",
        is_default: false,
      },
      {
        id: "lm",
        name: "LM",
        kind: "openai",
        locality: "network",
        is_default: false,
      },
    ],
  }),
}));

afterEach(cleanup);
beforeEach(() => {
  localStorage.setItem("argos-language", "ko");
  mocks.save.mutate.mockReset();
  mocks.modelsFor.mockReset();
  mocks.provider = "ollama";
});

test("provider select lists only ollama providers", () => {
  render(<ClassifierSection />);
  const select = screen.getByLabelText("모델 연결") as HTMLSelectElement;
  expect([...select.options].map((o) => o.textContent)).toEqual([
    "Home",
    "GPU",
  ]);
  expect(select.value).toBe("home");
});

test("saves provider with model", () => {
  render(<ClassifierSection />);
  fireEvent.change(screen.getByLabelText("모델 연결"), {
    target: { value: "gpu" },
  });
  expect(mocks.modelsFor).toHaveBeenLastCalledWith("gpu");
  fireEvent.click(screen.getByRole("radio", { name: /gemma4:e4b/ }));
  fireEvent.click(screen.getByRole("button", { name: "저장" }));
  expect(mocks.save.mutate).toHaveBeenCalledWith(
    expect.objectContaining({ model: "gemma4:e4b", provider_id: "gpu" }),
    expect.anything(),
  );
});

test("hermes hides provider select", () => {
  mocks.provider = "hermes";
  render(<ClassifierSection />);
  expect(screen.queryByLabelText("모델 연결")).toBeNull();
});
