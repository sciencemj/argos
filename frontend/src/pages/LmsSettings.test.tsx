import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { LmsSection } from "./LmsSettings";

const mocks = vi.hoisted(() => ({
  status: { data: {} as Record<string, unknown> },
  prepare: {
    data: undefined as Record<string, unknown> | undefined,
    mutate: vi.fn(),
    isPending: false,
  },
  connect: { mutate: vi.fn(), isPending: false },
  disconnect: { mutate: vi.fn(), isPending: false },
  courseNames: { mutate: vi.fn(), isPending: false },
  summary: { mutate: vi.fn(), isPending: false, error: null },
  tokenConnect: { mutate: vi.fn(), isPending: false, error: null },
  tokenDisconnect: { mutate: vi.fn(), isPending: false, error: null },
  syncNow: { mutate: vi.fn(), isPending: false, error: null },
  materials: { mutate: vi.fn(), isPending: false, error: null },
  merge: { mutate: vi.fn(), isPending: false, error: null },
}));
vi.mock("../api", () => ({
  useLmsStatus: () => mocks.status,
  usePrepareLmsExtension: () => mocks.prepare,
  useConnectLms: () => mocks.connect,
  useDisconnectLms: () => mocks.disconnect,
  useLmsCourseNames: () => mocks.courseNames,
  useLmsSummarySetting: () => mocks.summary,
  useConnectLmsToken: () => mocks.tokenConnect,
  useDisconnectLmsToken: () => mocks.tokenDisconnect,
  useSyncLmsNow: () => mocks.syncNow,
  useLmsMaterials: () => mocks.materials,
  useMergeLmsCourse: () => mocks.merge,
}));
vi.mock("../desktop", () => ({ inDesktopApp: () => false }));

afterEach(cleanup);

beforeEach(() => {
  localStorage.setItem("argos-language", "ko");
  mocks.status.data = {
    connected: false,
    clean_course_names: true,
    courses: [],
  };
  mocks.prepare.data = undefined;
  mocks.prepare.mutate.mockImplementation((_value, options) => {
    mocks.prepare.data = {
      extension_path:
        "/Users/test/Library/Application Support/Argos/browser-extensions/learningx",
    };
    options.onSuccess();
  });
});

test("the token form is the default and the extension sits behind a disclosure", () => {
  render(<LmsSection />);
  const details = screen
    .getByText("다른 연결 방법: 브라우저 확장 프로그램")
    .closest("details");
  expect(details).not.toHaveAttribute("open");
  const connect = screen.getByRole("button", { name: "연결" });
  expect(connect).toBeDisabled();
  fireEvent.change(screen.getByLabelText("LMS 주소"), {
    target: { value: "lms.school.ac.kr" },
  });
  const token = screen.getByLabelText("액세스 토큰");
  expect(token).toHaveAttribute("type", "password");
  fireEvent.change(token, { target: { value: " 7~abc " } });
  fireEvent.click(connect);
  expect(mocks.tokenConnect.mutate).toHaveBeenCalledWith(
    { url: "lms.school.ac.kr", token: "7~abc" },
    expect.anything(),
  );
  fireEvent.click(screen.getByRole("button", { name: "토큰 발급 방법" }));
  expect(screen.getByText(/새 액세스 토큰/)).toBeInTheDocument();
});

test("a connected token shows the account, sync state, errors and materials toggle", () => {
  mocks.status.data = {
    connected: false,
    clean_course_names: true,
    courses: [],
    token_url: "https://lms.school.ac.kr",
    token_user: "길동",
    token_last_sync: "2026-10-13T01:00:00Z",
    token_error: "LMS 토큰이 만료됐거나 취소됐어요.",
    token_warnings: ["알고리즘 공지: LMS 조회 실패 (500)"],
    materials: false,
  };
  render(<LmsSection />);
  expect(screen.getByText(/길동 계정으로 연결됨/)).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent("만료");
  expect(
    screen.getByText(/일부 항목을 가져오지 못했어요 \(1\)/),
  ).toBeInTheDocument();
  expect(screen.queryByLabelText("액세스 토큰")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "지금 가져오기" }));
  expect(mocks.syncNow.mutate).toHaveBeenCalled();
  fireEvent.click(
    screen.getByRole("checkbox", { name: /주차학습 자료도 저장/ }),
  );
  expect(mocks.materials.mutate).toHaveBeenCalledWith(true);
  fireEvent.click(screen.getByRole("button", { name: "토큰 바꾸기" }));
  expect(screen.getByLabelText("LMS 주소")).toHaveValue(
    "https://lms.school.ac.kr",
  );
});

test("installation guide prepares a stable folder and explains Chrome's manual steps", async () => {
  render(<LmsSection />);
  expect(screen.queryByLabelText("확장 프로그램 폴더")).not.toBeInTheDocument();
  fireEvent.click(
    screen.getByRole("button", { name: "확장 프로그램 설치 도우미" }),
  );
  expect(screen.getByText("chrome://extensions")).toBeInTheDocument();
  expect(
    screen.getByText("오른쪽 위 개발자 모드를 켜세요."),
  ).toBeInTheDocument();
  expect(screen.getByLabelText("확장 프로그램 폴더")).toHaveValue(
    String(mocks.prepare.data?.extension_path),
  );
  const writeText = vi.fn(async () => {});
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText },
  });
  fireEvent.click(screen.getByRole("button", { name: "폴더 경로 복사" }));
  await waitFor(() =>
    expect(writeText).toHaveBeenCalledWith(mocks.prepare.data?.extension_path),
  );
});

test("status distinguishes a minted code from a live connection and flags permission changes", () => {
  mocks.status.data = {
    connected: true,
    extension_seen_at: "2026-10-09T01:00:00Z",
    extension_manual_update: true,
  };
  render(<LmsSection />);
  expect(screen.getByText(/확장 프로그램 연결 확인/)).toBeInTheDocument();
  expect(screen.getByText(/권한 구성이 바뀌었어요/)).toBeInTheDocument();
});

test("course name toggle shows original and channel names", () => {
  mocks.status.data = {
    connected: true,
    clean_course_names: true,
    courses: [
      {
        channel_id: "c1",
        original: "262R (서울-학부)운영체제(OPERATING SYSTEMS)-02분반",
        name: "운영체제",
      },
    ],
  };
  render(<LmsSection />);
  expect(
    screen.getByText("262R (서울-학부)운영체제(OPERATING SYSTEMS)-02분반"),
  ).toBeInTheDocument();
  expect(screen.getByText("운영체제")).toBeInTheDocument();
  const toggle = screen.getByRole("checkbox", { name: /과목 이름 정리/ });
  expect(toggle).toBeChecked();
  fireEvent.click(toggle);
  expect(mocks.courseNames.mutate).toHaveBeenCalledWith(false);
});

test("the summary toggle turns notice summaries off and says when no model is set", () => {
  mocks.status.data = {
    connected: true,
    clean_course_names: true,
    courses: [],
    summary_min_chars: 300,
    summary_available: false,
  };
  render(<LmsSection />);
  expect(
    screen.getByText("인박스 분류 모델을 설정하면 긴 LMS 알림을 요약해요."),
  ).toBeInTheDocument();
  const toggle = screen.getByRole("checkbox", { name: /알림 AI 요약/ });
  expect(toggle).toBeChecked();
  fireEvent.click(toggle);
  expect(mocks.summary.mutate).toHaveBeenCalledWith(0);
});

test("a course can be merged into one of my channels after confirming", () => {
  mocks.status.data = {
    connected: false,
    clean_course_names: true,
    courses: [
      {
        channel_id: "lms1",
        original: "262R (서울-학부)기계학습(MACHINE LEARNING)-02분반",
        name: "기계학습",
        course: true,
      },
      {
        channel_id: "inbox",
        original: "알림·메시지",
        name: "알림·메시지",
        course: false,
      },
    ],
    merge_targets: [{ channel_id: "mine", name: "머신러닝" }],
  };
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<LmsSection />);
  expect(
    screen.queryByLabelText("알림·메시지 합칠 채널"),
  ).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("기계학습 합칠 채널"), {
    target: { value: "mine" },
  });
  expect(confirm).toHaveBeenCalled();
  expect(mocks.merge.mutate).toHaveBeenCalledWith({
    channelId: "lms1",
    targetId: "mine",
  });
  confirm.mockRestore();
});
