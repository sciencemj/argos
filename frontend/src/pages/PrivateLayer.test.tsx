import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { Agent } from "../api";
import { PrivateGhost, PrivateOverlay } from "./PrivateLayer";

const encoder = new TextEncoder();
const line = (value: unknown) => encoder.encode(`${JSON.stringify(value)}\n`);

let posted: { url: string; body: unknown }[] = [];

/** A local model that answers every private turn with the given tokens. */
function stubModel(tokens: string[]) {
  posted = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      posted.push({ url, body: JSON.parse(String(init.body)) });
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          for (const token of tokens) controller.enqueue(line({ token }));
          controller.enqueue(line({ done: true }));
          controller.close();
        },
      });
      return new Response(stream, {
        headers: { "Content-Type": "application/x-ndjson" },
      });
    }),
  );
}

let reduced = false;
let count = 0;
let agent: Agent;
beforeEach(() => {
  count += 1;
  reduced = false;
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query.includes("reduce") ? reduced : false,
  }));
  agent = {
    id: `a${count}`,
    name: `local${count}`,
    display_name: "Gemma",
    private_ok: true,
  } as Agent;
  stubModel(["안녕", "하세요"]);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** The DM as DMPage puts it together: the ghost in the header, the layer over <main>. */
function DM({ who = agent }: { who?: Agent }) {
  return (
    <main data-testid="main">
      <header>
        <PrivateGhost agent={who} />
      </header>
      <PrivateOverlay agent={who} />
    </main>
  );
}

const layer = () => screen.queryByRole("region", { name: "비공개 대화" });
const ghost = () => screen.getByRole("button", { name: "비공개 대화로 전환" });

function openPrivate(x = 300, y = 80) {
  const main = screen.getByTestId("main");
  vi.spyOn(main, "getBoundingClientRect").mockReturnValue({
    left: 100,
    top: 50,
    right: 900,
    bottom: 650,
    width: 800,
    height: 600,
    x: 100,
    y: 50,
    toJSON: () => ({}),
  });
  fireEvent.click(ghost(), { clientX: x, clientY: y });
}

async function sendText(text: string) {
  const input = screen.getByRole("textbox", { name: "비공개 메시지 입력" });
  fireEvent.change(input, { target: { value: text } });
  fireEvent.keyDown(input, { key: "Enter" });
}

test("ghost button only when private_ok", () => {
  const { unmount } = render(<DM />);
  expect(ghost()).toHaveAttribute("aria-pressed", "false");
  unmount();
  render(<DM who={{ ...agent, private_ok: false }} />);
  expect(
    screen.queryByRole("button", { name: "비공개 대화로 전환" }),
  ).toBeNull();
  expect(layer()).toBeNull();
});

test("toggling opens and closes the layer with origin", () => {
  render(<DM />);
  expect(layer()).toBeNull(); // closed: hidden from the accessibility tree
  openPrivate(300, 80);
  const shown = layer();
  expect(shown).not.toBeNull();
  expect(shown).toHaveAttribute("data-open", "true");
  expect(shown?.style.getPropertyValue("--x")).toBe("200px");
  expect(shown?.style.getPropertyValue("--y")).toBe("30px");
  // The header ghost now says it goes back.
  expect(
    screen.getAllByRole("button", { name: "일반 대화로 돌아가기" }).length,
  ).toBeGreaterThan(0);
  fireEvent.click(
    within(shown as HTMLElement).getByRole("button", {
      name: "일반 대화로 돌아가기",
    }),
    { clientX: 260, clientY: 70 },
  );
  expect(layer()).toBeNull();
  expect(
    document.querySelector(".private-layer")?.getAttribute("data-open"),
  ).toBe("false");
});

test("shows the banner and the empty-state line", () => {
  render(<DM />);
  openPrivate();
  const shown = layer() as HTMLElement;
  expect(
    within(shown).getByText(
      "비공개 대화 · 이 기기와 내 네트워크의 모델만 쓰고, 앱을 끄면 사라져요",
    ),
  ).toBeInTheDocument();
  expect(
    within(shown).getByText("여기서 나눈 대화는 어디에도 저장되지 않아요"),
  ).toBeInTheDocument();
  expect(within(shown).getByText("비공개 · 저장 안 됨")).toBeInTheDocument();
});

test("keeps private turns across toggles and remounts", async () => {
  const stored = vi.spyOn(Storage.prototype, "setItem");
  const first = render(<DM />);
  openPrivate();
  await sendText("비밀 메모");
  await waitFor(() =>
    expect(within(layer() as HTMLElement).getByText("안녕하세요")),
  );
  // Back to the normal DM, then to another channel (unmount)…
  fireEvent.click(
    within(layer() as HTMLElement).getByRole("button", {
      name: "일반 대화로 돌아가기",
    }),
  );
  first.unmount();
  // …and back: still closed as left, and the conversation is still there.
  const second = render(<DM />);
  expect(layer()).toBeNull();
  openPrivate();
  expect(
    within(layer() as HTMLElement).getByText("비밀 메모"),
  ).toBeInTheDocument();
  expect(
    within(layer() as HTMLElement).getByText("안녕하세요"),
  ).toBeInTheDocument();
  // Open state also survives leaving the channel while private.
  second.unmount();
  render(<DM />);
  expect(layer()).not.toBeNull();
  expect(stored).not.toHaveBeenCalled();
});

test("sends with Enter but not while composing", async () => {
  render(<DM />);
  openPrivate();
  const input = screen.getByRole("textbox", { name: "비공개 메시지 입력" });
  fireEvent.change(input, { target: { value: "가" } });
  fireEvent.keyDown(input, { key: "Enter", isComposing: true });
  fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
  expect(posted).toHaveLength(0);
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(posted).toHaveLength(1));
  expect(posted[0].url).toContain(`/agents/${agent.name}/private`);
  expect(input).toHaveValue("");
});

test("clear button empties", async () => {
  render(<DM />);
  openPrivate();
  await sendText("지울 글");
  await waitFor(() =>
    expect(within(layer() as HTMLElement).getByText("안녕하세요")),
  );
  fireEvent.click(screen.getByRole("button", { name: "대화 지우기" }));
  expect(screen.queryByText("지울 글")).toBeNull();
  expect(
    screen.getByText("여기서 나눈 대화는 어디에도 저장되지 않아요"),
  ).toBeInTheDocument();
});

test("reduced motion skips animation class", () => {
  const { unmount } = render(<DM />);
  openPrivate();
  expect(layer()).toHaveClass("private-animate");
  expect(document.querySelector(".private-ring")).not.toBeNull();
  act(() => {
    fireEvent.click(
      within(layer() as HTMLElement).getByRole("button", {
        name: "일반 대화로 돌아가기",
      }),
    );
  });
  unmount();
  reduced = true;
  render(<DM />);
  openPrivate();
  expect(layer()).not.toHaveClass("private-animate");
  expect(document.querySelector(".private-ring")).toBeNull();
});
