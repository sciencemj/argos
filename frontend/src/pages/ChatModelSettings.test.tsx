import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { selectValue } from "../test-select";
import { ChatModelSection } from "./ChatModelSettings";

const mocks = vi.hoisted(() => ({ save: vi.fn() }));
vi.mock("../api", () => ({
  useChatSettings: () => ({
    data: {
      provider_id: "home",
      model: "qwen3.5:9b",
      effort: null,
      provider_name: "Home",
      resolved_model: "qwen3.5:9b",
      problem: null,
    },
  }),
  useProviders: () => ({
    data: [
      { id: "home", name: "Home" },
      { id: "gpu", name: "GPU" },
    ],
  }),
  useChatModels: () => ({
    data: {
      models: [
        {
          provider_id: "home",
          provider_name: "Home",
          model: "qwen3.5:9b",
          remote: false,
        },
        {
          provider_id: "gpu",
          provider_name: "GPU",
          model: "llama3",
          remote: false,
        },
      ],
      errors: [],
    },
  }),
  useSaveChatSettings: () => ({
    mutate: mocks.save,
    isPending: false,
    error: null,
  }),
}));

beforeEach(() => {
  localStorage.setItem("argos-language", "ko");
  mocks.save.mockReset();
});
afterEach(cleanup);

test("changing the connection clears the model picked on the old one", () => {
  render(<ChatModelSection />);
  selectValue(screen.getByLabelText("모델 연결"), "gpu");
  expect(mocks.save).toHaveBeenCalledWith({ provider_id: "gpu", model: null });
});

test("models are those of the chosen connection", () => {
  render(<ChatModelSection />);
  selectValue(screen.getByLabelText("모델"), "qwen3.5:9b");
  expect(() => selectValue(screen.getByLabelText("모델"), "llama3")).toThrow();
});
