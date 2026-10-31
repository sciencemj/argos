import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, expect, test, vi } from "vitest";
import { ProviderSection } from "./ProviderSettings";

const mocks = vi.hoisted(() => ({
  providers: { data: [] as Record<string, unknown>[] },
  save: { mutate: vi.fn(), isPending: false, error: null as unknown },
  remove: { mutate: vi.fn(), isPending: false, error: null as unknown },
  test: {
    mutate: vi.fn(),
    reset: vi.fn(),
    isPending: false,
    error: null as unknown,
    data: undefined as Record<string, unknown> | undefined,
  },
  models: { data: undefined as Record<string, unknown> | undefined },
}));
vi.mock("../api", () => ({
  useProviders: () => mocks.providers,
  useSaveProvider: () => mocks.save,
  useDeleteProvider: () => mocks.remove,
  useTestProvider: () => mocks.test,
  useProviderModels: () => mocks.models,
}));

const DEVICE = {
  id: "p1",
  name: "Local",
  kind: "ollama",
  base_url: "http://127.0.0.1:11434/v1",
  has_api_key: false,
  strict_locality: null,
  locality: "device",
  is_default: true,
};
const NETWORK = {
  id: "p2",
  name: "Lab box",
  kind: "openai",
  base_url: "http://192.0.2.10:8080/v1",
  has_api_key: true,
  strict_locality: null,
  locality: "network",
  is_default: false,
};

beforeAll(() => {
  // jsdom has <dialog> but not its modal methods.
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.open = true;
  };
  HTMLDialogElement.prototype.close = function close() {
    this.open = false;
  };
});

afterEach(cleanup);

beforeEach(() => {
  localStorage.setItem("argos-language", "ko");
  mocks.providers.data = [DEVICE, NETWORK];
  mocks.save.mutate.mockReset();
  mocks.save.error = null;
  mocks.remove.mutate.mockReset();
  mocks.remove.error = null;
  mocks.test.mutate.mockReset();
  mocks.test.data = undefined;
  mocks.models.data = undefined;
});

const rowOf = (name: string) => {
  const row = screen.getByText(name).closest("li");
  if (!row) throw new Error(`no row for ${name}`);
  return within(row as HTMLElement);
};

test("lists providers with locality badge", () => {
  render(<ProviderSection />);
  const first = rowOf("Local");
  expect(first.getByText("http://127.0.0.1:11434/v1")).toBeInTheDocument();
  expect(first.getByText("이 기기")).toBeInTheDocument();
  expect(first.queryByRole("button", { name: "삭제" })).toBeNull();
  const second = rowOf("Lab box");
  expect(second.getByText("http://192.0.2.10:8080/v1")).toBeInTheDocument();
  expect(second.getByText("내 네트워크")).toBeInTheDocument();
  expect(second.getByRole("button", { name: "삭제" })).toBeInTheDocument();
});

test("adds a provider after a successful test", () => {
  const { rerender } = render(<ProviderSection />);
  fireEvent.click(screen.getByRole("button", { name: "연결 추가" }));
  fireEvent.change(screen.getByLabelText("이름"), { target: { value: "GPU" } });
  fireEvent.change(screen.getByLabelText("종류"), {
    target: { value: "ollama" },
  });
  fireEvent.change(screen.getByLabelText("주소"), {
    target: { value: "http://gpu.test:11434" },
  });
  fireEvent.click(screen.getByRole("button", { name: "연결 시험" }));
  expect(mocks.test.mutate).toHaveBeenCalledWith({
    kind: "ollama",
    base_url: "http://gpu.test:11434",
    api_key: null,
  });
  mocks.test.data = {
    reachable: true,
    error: null,
    locality: "network",
    models: [
      { name: "a", remote: false, parameter_size: null, decision: false },
      { name: "b", remote: false, parameter_size: null, decision: false },
      { name: "c", remote: false, parameter_size: null, decision: false },
    ],
  };
  rerender(<ProviderSection />);
  expect(screen.getByText("모델 3개")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "저장" }));
  expect(mocks.save.mutate).toHaveBeenCalledWith(
    {
      name: "GPU",
      kind: "ollama",
      base_url: "http://gpu.test:11434",
      api_key: null,
      strict_locality: null,
    },
    expect.anything(),
  );
  expect(mocks.save.mutate.mock.calls[0][0]).not.toHaveProperty("editing");
});

test("warns for cloud locality", () => {
  const { rerender } = render(<ProviderSection />);
  fireEvent.click(screen.getByRole("button", { name: "연결 추가" }));
  expect(screen.queryByRole("alert")).toBeNull();
  mocks.test.data = {
    reachable: true,
    error: null,
    locality: "cloud",
    models: [],
  };
  rerender(<ProviderSection />);
  expect(screen.getByRole("alert")).toHaveTextContent(
    "대화 내용이 이 컴퓨터와 내 네트워크 밖으로 나가요",
  );
});

test("api key field never shows the saved key", () => {
  render(<ProviderSection />);
  fireEvent.click(rowOf("Lab box").getByRole("button", { name: "수정" }));
  const key = screen.getByLabelText("API 키 (선택)");
  expect(key).toHaveValue("");
  expect(key).toHaveAttribute("type", "password");
  expect(screen.getByText("저장됨")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "저장" }));
  const kept = mocks.save.mutate.mock.calls[0][0];
  expect(kept).toMatchObject({ editing: "p2", name: "Lab box" });
  expect(kept).not.toHaveProperty("api_key");

  fireEvent.click(screen.getByRole("button", { name: "키 지우기" }));
  fireEvent.click(screen.getByRole("button", { name: "저장" }));
  expect(mocks.save.mutate.mock.calls[1][0]).toMatchObject({
    editing: "p2",
    api_key: "",
  });
});

test("delete error shows users", () => {
  mocks.remove.error = new Error("@tutor, 인박스 분류에서 쓰는 연결이에요");
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<ProviderSection />);
  fireEvent.click(rowOf("Lab box").getByRole("button", { name: "삭제" }));
  expect(mocks.remove.mutate).toHaveBeenCalledWith("p2");
  expect(screen.getByRole("alert")).toHaveTextContent(
    "@tutor, 인박스 분류에서 쓰는 연결이에요",
  );
});
