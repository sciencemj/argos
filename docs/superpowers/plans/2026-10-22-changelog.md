# 앱 안 변경 내역 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 저장소의 한국어 `CHANGELOG.md`를 원본으로, 업데이트 뒤 처음 열 때 새 내역을 모달로 한 번 보여 주고 설정에서 다시 보게 하며, 릴리스 알림·GitHub 릴리스 본문도 같은 내용을 쓴다.

**Architecture:** 프런트 빌드가 `CHANGELOG.md`를 `?raw`로 묶고 순수 함수(`changelog.ts`)로 파싱한다. 본 버전은 서버 `app_setting.changelog_seen`(GET/PUT API)에 둔다. 현재 버전은 데스크톱 `updateStatus().current`. 릴리스 스크립트가 같은 파일을 검사·추출한다.

**Tech Stack:** React 19, Vite(`?raw`), TanStack Query, vitest, FastAPI, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-22-changelog-design.md`

## Global Constraints

- 형식: `## <x.y.z> · <YYYY-MM-DD>` 제목, 그 아래 `- ` 목록. 목록 항목 안 인라인 마크다운만. 본문은 한국어만.
- 본 버전은 서버(`app_setting.changelog_seen`), `services.set_setting`(활동 기록 남김). semver가 아니면 422.
- 첫 설치(seen 없음)는 모달 없이 seen=current만 기록. 데스크톱이 아니면(현재 버전 없음) 자동 모달 없음. 환영 화면 중엔 안 띄움.
- 디자인 캔버스가 없어 기존 컴포넌트(`card`, `btn`, `Markdown`)와 토큰 클래스만. UI 문구는 `t(한국어, English)`.
- 명령은 worktree 루트에서. git은 `/usr/bin/git`.
- API 모델 변경 커밋에는 `make api-types` 결과를 함께.

## Review Focus

- `0.1.9`와 `0.1.10` 비교가 문자열 비교로 뒤집히면 안 된다 → Task 1 `compareVersions` 테스트.
- seen이 current보다 새 버전이면(다운그레이드·개발 빌드) 모달을 띄우지 않고 seen을 낮추지도 않는다 → Task 1 `releasesAfter`와 Task 3 테스트.
- 업데이트로 여러 버전을 건너뛰면 그 사이 버전 내역이 전부(최신 순) 보여야 한다 → Task 1 `releasesAfter` 테스트.
- 형식이 틀린 제목(`## 다음`, 날짜 없음)은 앱을 깨지 말고 무시 → Task 1 파서 테스트.
- 개발 서버(`make dev`)에서 루트 밖 `CHANGELOG.md`를 못 읽어 화면이 하얗게 되면 안 된다 → Task 3 Step(Vite `server.fs.allow`)과 수동 확인.

---

## File Structure

- Create `CHANGELOG.md` (루트)
- Create `frontend/src/changelog.ts` (파서·비교·선택, 순수)
- Create `frontend/src/changelog.test.ts`
- Create `frontend/src/whatsNew.tsx` (`ChangelogList`, `ChangelogDialog`, `WhatsNew`, `ChangelogButton`). `changelog.ts`와 같은 이름의 `.tsx`를 두면 import 해석이 모호해서 이름을 달리함
- Create `frontend/src/whatsNew.test.tsx`
- Modify `frontend/vite.config.ts` (`server.fs.allow`)
- Modify `frontend/src/api.ts` (`useChangelogSeen`, `useSaveChangelogSeen`)
- Modify `frontend/src/layout/Shell.tsx` (`<WhatsNew />`)
- Modify `frontend/src/pages/OpsSettings.tsx` (`AppUpdateSection`에 "변경 내역" 버튼)
- Modify `backend/src/argos/api.py` (changelog API) + `backend/tests/test_changelog.py`
- Modify `frontend/src/api-types.ts` (생성)
- Modify `Makefile`, `.github/workflows/release.yml`
- Modify `docs/PLAN.md`, `docs/decisions.md`, `CLAUDE.md`

---

### Task 1: CHANGELOG.md와 파서

**Files:**
- Create: `CHANGELOG.md`
- Create: `frontend/src/changelog.ts`
- Test: `frontend/src/changelog.test.ts`

**Interfaces:**
- Produces:
  - `type Release = { version: string; date: string; items: string[] }`
  - `parseChangelog(raw: string): Release[]` (파일 순서 = 최신 순)
  - `compareVersions(a: string, b: string): number`
  - `releasesAfter(list: Release[], seen: string, current: string): Release[]` (seen < v ≤ current, 최신 순)
  - `RELEASES: Release[]` (번들된 파일을 파싱한 값)

- [ ] **Step 1: Write the failing test** (`changelog.test.ts`)

```ts
import { expect, test } from "vitest";
import tauri from "../../desktop/src-tauri/tauri.conf.json";
import {
  compareVersions,
  parseChangelog,
  RELEASES,
  releasesAfter,
} from "./changelog";

const SAMPLE = `# 변경 내역

## 0.1.10 · 2026-10-20

- 마감이 **캘린더**에 보여요.
- 설정에 \`변경 내역\` 버튼

## 다음

- 버전이 아닌 제목은 무시

## 0.1.9 · 2026-10-10

- 첫 항목
`;

test("reads versions, dates and items in file order", () => {
  expect(parseChangelog(SAMPLE)).toEqual([
    {
      version: "0.1.10",
      date: "2026-10-20",
      items: ["마감이 **캘린더**에 보여요.", "설정에 `변경 내역` 버튼"],
    },
    { version: "0.1.9", date: "2026-10-10", items: ["첫 항목"] },
  ]);
});

test("compares versions as numbers", () => {
  expect(compareVersions("0.1.9", "0.1.10")).toBeLessThan(0);
  expect(compareVersions("1.0.0", "0.9.9")).toBeGreaterThan(0);
  expect(compareVersions("0.1.16", "0.1.16")).toBe(0);
});

test("picks every release since the one seen, newest first", () => {
  const list = parseChangelog(SAMPLE);
  expect(releasesAfter(list, "0.1.8", "0.1.10").map((r) => r.version)).toEqual(
    ["0.1.10", "0.1.9"],
  );
  expect(releasesAfter(list, "0.1.10", "0.1.10")).toEqual([]);
  expect(releasesAfter(list, "0.2.0", "0.1.10")).toEqual([]); // seen is newer
  expect(releasesAfter(list, "0.1.8", "0.1.9").map((r) => r.version)).toEqual(
    ["0.1.9"],
  );
});

test("the bundled changelog covers the app version", () => {
  expect(RELEASES.length).toBeGreaterThan(0);
  expect(compareVersions(RELEASES[0].version, tauri.version)).toBeGreaterThanOrEqual(0);
  for (const r of RELEASES) expect(r.items.length).toBeGreaterThan(0);
});
```

(`tsconfig.app.json`에 `resolveJsonModule`이 없으면 JSON import가 tsc에서 실패한다. `grep -n resolveJsonModule frontend/tsconfig.app.json`로 확인하고, 없으면 테스트에서 `const tauri = JSON.parse(readFileSync(new URL("../../desktop/src-tauri/tauri.conf.json", import.meta.url), "utf8"))`(`node:fs`)로 읽는다.)

- [ ] **Step 2: Run to verify fail**

Run: `cd frontend && bunx vitest run src/changelog.test.ts`
Expected: FAIL (모듈 없음).

- [ ] **Step 3: Write `CHANGELOG.md`**

v0.1.1~v0.1.16 태그 사이 커밋으로 소급 작성. 각 버전의 커밋 목록:

```bash
for v in $(/usr/bin/git tag --sort=v:refname); do echo "== $v $(/usr/bin/git log -1 --format=%cs $v)"; done
/usr/bin/git log --format='%s' v0.1.15..v0.1.16 | grep -E '^(feat|fix)'
```

규칙: `feat`·`fix`만 사용자 관점 한국어 한 줄로(`docs`·`chore`·`test`·`refactor` 제외), 버전당 1~6줄, 최신 버전이 맨 위. `feat`·`fix`가 없는 버전은 "작은 개선과 안정성 수정" 한 줄. 날짜는 태그 커밋 날짜. 파일 맨 위:

```markdown
# 변경 내역

Argos의 버전별 변경 사항이에요. 업데이트 후 앱에서도 볼 수 있어요.

## 0.1.16 · 2026-10-21

- …
```

(위 `0.1.16` 날짜·항목은 실제 git 기록으로 채운다.)

- [ ] **Step 4: Implement `changelog.ts`**

```ts
import raw from "../../CHANGELOG.md?raw";

/** One version's notes in CHANGELOG.md (Phase 19). */
export type Release = { version: string; date: string; items: string[] };

const HEADING = /^## (\d+\.\d+\.\d+) · (\d{4}-\d{2}-\d{2})\s*$/;

/** `## x.y.z · YYYY-MM-DD` sections with `- ` items, in file order (newest first).
 * Other headings and their items are skipped. */
export function parseChangelog(text: string): Release[] {
  const releases: Release[] = [];
  let current: Release | null = null;
  for (const line of text.split(/\r?\n/)) {
    if (line.startsWith("## ")) {
      const match = HEADING.exec(line);
      current = match ? { version: match[1], date: match[2], items: [] } : null;
      if (current) releases.push(current);
    } else if (current && line.startsWith("- ")) {
      current.items.push(line.slice(2).trim());
    }
  }
  return releases;
}

export function compareVersions(a: string, b: string): number {
  const x = a.split(".").map(Number);
  const y = b.split(".").map(Number);
  for (let i = 0; i < 3; i++) {
    if (x[i] !== y[i]) return (x[i] ?? 0) - (y[i] ?? 0);
  }
  return 0;
}

/** Releases newer than the one seen, up to the running version. */
export function releasesAfter(
  list: Release[],
  seen: string,
  current: string,
): Release[] {
  return list.filter(
    (r) =>
      compareVersions(r.version, seen) > 0 &&
      compareVersions(r.version, current) <= 0,
  );
}

export const RELEASES = parseChangelog(raw);
```

- [ ] **Step 5: Run tests**

Run: `cd frontend && bunx vitest run src/changelog.test.ts && bunx tsc -b && bunx biome check src`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
/usr/bin/git add CHANGELOG.md frontend/src/changelog.ts frontend/src/changelog.test.ts
/usr/bin/git commit -m "feat: changelog file and parser"
```

---

### Task 2: 본 버전 API

**Files:**
- Modify: `backend/src/argos/api.py` (설정 라우트 근처, 예: `/settings/icloud/options` 앞)
- Create: `backend/tests/test_changelog.py`
- Modify: `frontend/src/api.ts`, `frontend/src/api-types.ts`

**Interfaces:**
- Produces: `GET /api/v1/settings/changelog` → `ChangelogSeen{seen: str | None}`, `PUT /api/v1/settings/changelog` body `ChangelogSeen{seen: str}`(semver) → `ChangelogSeen`. 프런트 `useChangelogSeen()`(query key `["settings","changelog"]`), `useSaveChangelogSeen()` mutation(`(seen: string) => ...`).

- [ ] **Step 1: Write failing test** (`test_changelog.py`)

```python
"""Which changelog version the user has seen (Phase 19)."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from argos.config import Settings
from argos.main import create_app


@pytest.fixture
def app(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as client:
        yield client


def test_nothing_seen_on_a_new_install(app: TestClient) -> None:
    assert app.get("/api/v1/settings/changelog").json() == {"seen": None}


def test_seen_version_is_kept(app: TestClient) -> None:
    assert app.put("/api/v1/settings/changelog", json={"seen": "0.1.17"}).json() == {
        "seen": "0.1.17"
    }
    assert app.get("/api/v1/settings/changelog").json() == {"seen": "0.1.17"}


@pytest.mark.parametrize("bad", ["", "latest", "0.1", "0.1.17-beta", None])
def test_only_plain_versions_are_accepted(app: TestClient, bad: str | None) -> None:
    assert app.put("/api/v1/settings/changelog", json={"seen": bad}).status_code == 422
```

- [ ] **Step 2: Run to verify fail**

Run: `cd backend && uv run pytest tests/test_changelog.py -q`
Expected: FAIL (404).

- [ ] **Step 3: Implement** (`api.py`)

```python
CHANGELOG_SEEN = "changelog_seen"


class ChangelogSeen(BaseModel):
    seen: str | None = Field(pattern=r"^\d+\.\d+\.\d+$")


class ChangelogSeenIn(BaseModel):
    seen: str = Field(pattern=r"^\d+\.\d+\.\d+$")


@router.get("/settings/changelog")
async def get_changelog_seen(session: Session) -> ChangelogSeen:
    """The last version whose changelog the user dismissed (Phase 19)."""
    return ChangelogSeen(seen=await services.get_setting(session, CHANGELOG_SEEN))


@router.put("/settings/changelog")
async def set_changelog_seen(session: Session, body: ChangelogSeenIn) -> ChangelogSeen:
    await services.set_setting(session, CHANGELOG_SEEN, body.seen, USER)
    return ChangelogSeen(seen=body.seen)
```

(`ChangelogSeen`의 `seen`은 응답 모델이라 `Field(default=None, ...)`로 두면 OpenAPI가 nullable로 나온다. pydantic이 `None`에 pattern을 적용하지 않으므로 GET 기본값 `None`은 통과한다.)

`api.ts` (설정 훅 근처):

```ts
export const useChangelogSeen = () =>
  useQuery({
    queryKey: ["settings", "changelog"],
    queryFn: () => call(client.GET("/api/v1/settings/changelog")),
  });

export const useSaveChangelogSeen = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (seen: string) =>
      call(client.PUT("/api/v1/settings/changelog", { body: { seen } })),
    onSuccess: (data) => qc.setQueryData(["settings", "changelog"], data),
  });
};
```

`app_setting` 변경 WS 이벤트(`object_type="app_setting"`)가 이미 `invalidateFor`에서 `["settings", …]`를 무효화하는지 `grep -n "app_setting" frontend/src/api.ts`로 확인. 없으면 그대로 둔다(이 값은 이 화면만 씀).

- [ ] **Step 4: Run tests, types**

Run: `cd backend && uv run pytest tests/test_changelog.py -q && uv run ruff check src tests && uv run pyright`, 루트에서 `make api-types`, `cd frontend && bunx tsc -b`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
/usr/bin/git add backend/src/argos/api.py backend/tests/test_changelog.py frontend/src/api.ts frontend/src/api-types.ts
/usr/bin/git commit -m "feat: remember the changelog version the user has seen"
```

---

### Task 3: 새 기능 모달과 설정 버튼

**Files:**
- Create: `frontend/src/whatsNew.tsx`
- Create: `frontend/src/whatsNew.test.tsx`
- Modify: `frontend/src/layout/Shell.tsx:107-130`
- Modify: `frontend/src/pages/OpsSettings.tsx:435-500`
- Modify: `frontend/vite.config.ts`

**Interfaces:**
- Consumes: `RELEASES`, `releasesAfter`, `compareVersions` (Task 1), `useChangelogSeen`, `useSaveChangelogSeen` (Task 2), `updateStatus`, `inDesktopApp` (`desktop.ts`), `Markdown` (`markdown.tsx`).
- Produces: `ChangelogDialog({ releases, title, onClose })`, `WhatsNew()`, `ChangelogButton()`.

- [ ] **Step 1: Write failing tests** (`whatsNew.test.tsx`)

```tsx
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { parseChangelog } from "./changelog";
import { WhatsNew } from "./whatsNew";

const mocks = vi.hoisted(() => ({
  seen: { data: undefined as { seen: string | null } | undefined },
  save: { mutate: vi.fn() },
  current: "0.1.10" as string | undefined,
}));
vi.mock("./api", () => ({
  useChangelogSeen: () => mocks.seen,
  useSaveChangelogSeen: () => mocks.save,
}));
vi.mock("./desktop", () => ({
  inDesktopApp: () => mocks.current !== undefined,
  updateStatus: () =>
    mocks.current ? Promise.resolve({ current: mocks.current }) : undefined,
}));

const RELEASES = parseChangelog(
  "## 0.1.10 · 2026-10-20\n\n- 새 기능\n\n## 0.1.9 · 2026-10-10\n\n- 이전 기능\n",
);

afterEach(cleanup);
beforeEach(() => {
  localStorage.setItem("argos-language", "ko");
  mocks.save.mutate.mockReset();
  mocks.current = "0.1.10";
});

test("a new install records the version quietly", async () => {
  mocks.seen.data = { seen: null };
  render(<WhatsNew releases={RELEASES} />);
  await waitFor(() => expect(mocks.save.mutate).toHaveBeenCalledWith("0.1.10"));
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("after an update the new notes show once", async () => {
  mocks.seen.data = { seen: "0.1.8" };
  render(<WhatsNew releases={RELEASES} />);
  expect(await screen.findByRole("dialog")).toBeTruthy();
  expect(screen.getByText("새 기능")).toBeTruthy();
  expect(screen.getByText("이전 기능")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "확인" }));
  expect(mocks.save.mutate).toHaveBeenCalledWith("0.1.10");
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("nothing shows when up to date, newer, or outside the app", async () => {
  mocks.seen.data = { seen: "0.1.10" };
  const { unmount } = render(<WhatsNew releases={RELEASES} />);
  await Promise.resolve();
  expect(screen.queryByRole("dialog")).toBeNull();
  unmount();

  mocks.seen.data = { seen: "0.2.0" };
  render(<WhatsNew releases={RELEASES} />);
  await Promise.resolve();
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(mocks.save.mutate).not.toHaveBeenCalled();
  cleanup();

  mocks.current = undefined;
  mocks.seen.data = { seen: "0.1.8" };
  render(<WhatsNew releases={RELEASES} />);
  await Promise.resolve();
  expect(screen.queryByRole("dialog")).toBeNull();
});
```


- [ ] **Step 2: Run to verify fail**

Run: `cd frontend && bunx vitest run src/whatsNew.test.tsx`
Expected: FAIL (모듈 없음).

- [ ] **Step 3: Implement `whatsNew.tsx`**

```tsx
import { useEffect, useState } from "react";
import { useChangelogSeen, useSaveChangelogSeen } from "./api";
import { RELEASES, type Release, releasesAfter } from "./changelog";
import { inDesktopApp, updateStatus } from "./desktop";
import { t, tt } from "./i18n";
import { Markdown } from "./markdown";
import { btn, card } from "./ui";

/** Versions with their notes, newest first (CHANGELOG.md, Phase 19). */
export function ChangelogList({ releases }: { releases: Release[] }) {
  return (
    <div className="flex flex-col gap-5">
      {releases.map((r) => (
        <section key={r.version} className="flex flex-col gap-2">
          <h3 className="m-0 flex items-baseline gap-2 text-[15px] font-medium text-ink">
            <span className="font-mono">v{r.version}</span>
            <span className="text-[12px] font-normal text-meta">{r.date}</span>
          </h3>
          <ul className="m-0 flex list-disc flex-col gap-1 pl-5 text-[13.5px] leading-relaxed text-text-2">
            {r.items.map((item) => (
              <li key={item}>
                <Markdown text={item} />
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

export function ChangelogDialog({
  releases,
  title,
  onClose,
}: {
  releases: Release[];
  title: string;
  onClose: () => void;
}) {
  useEffect(() => {
    const key = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 grid place-items-center p-4">
      <button
        type="button"
        aria-label={t("닫기", "Close")}
        tabIndex={-1}
        className="absolute inset-0 cursor-default bg-black/30"
        onClick={onClose}
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={`${card} relative flex max-h-[80vh] w-[520px] max-w-full flex-col gap-4 p-6 shadow-lift`}
      >
        <h2 className="m-0 text-[20px] font-light tracking-[-0.02em] text-ink">
          {title}
        </h2>
        <div className="min-h-0 overflow-y-auto">
          <ChangelogList releases={releases} />
        </div>
        <div className="flex justify-end">
          <button type="button" className={btn.cta} onClick={onClose}>
            {t("확인", "OK")}
          </button>
        </div>
      </div>
    </div>
  );
}

/** After an update, the notes since the version last seen, once (Phase 19). */
export function WhatsNew({ releases = RELEASES }: { releases?: Release[] }) {
  const seen = useChangelogSeen();
  const save = useSaveChangelogSeen();
  const [current, setCurrent] = useState<string | null>(null);
  const [open, setOpen] = useState<Release[] | null>(null);

  useEffect(() => {
    if (!inDesktopApp()) return;
    void updateStatus()?.then((s) => s && setCurrent(s.current));
  }, []);

  useEffect(() => {
    // open !== null: already shown (or dismissed) in this session; don't reopen
    // before the saved version comes back.
    if (!current || !seen.data || open !== null) return;
    if (seen.data.seen === null) {
      save.mutate(current); // a new install has nothing to catch up on
      return;
    }
    const fresh = releasesAfter(releases, seen.data.seen, current);
    if (fresh.length) setOpen(fresh);
  }, [current, seen.data, releases, open, save]);

  if (!open?.length || !current) return null;
  return (
    <ChangelogDialog
      releases={open}
      title={tt`Argos ${current}에서 바뀐 점`}
      onClose={() => {
        save.mutate(current);
        setOpen([]);
      }}
    />
  );
}

/** Settings → app version: the whole changelog on demand. */
export function ChangelogButton() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" className={btn.ghost} onClick={() => setOpen(true)}>
        {t("변경 내역", "What's new")}
      </button>
      {open && (
        <ChangelogDialog
          releases={RELEASES}
          title={t("변경 내역", "What's new")}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}
```

`tt` 템플릿의 영어 번역은 `i18n` 사전(`enTemplates`)에 `"Argos {0}에서 바뀐 점": "What's new in Argos {0}"`을 추가한다(`grep -n "enTemplates" frontend/src/i18n*`로 위치 확인).

- [ ] **Step 4: Wire it in**

`Shell.tsx`: import `{ WhatsNew } from "../whatsNew"`, 데스크톱 레이아웃의 `<Toaster />` 아래와 `MobileShell`을 감싸는 반환 앞에서도 보이도록 `MobileShell` 반환에 `children` 다음 `<WhatsNew />` 추가. (환영 화면은 Shell이 `Navigate`로 먼저 돌려보내므로 그 중엔 렌더되지 않는다.)

`OpsSettings.tsx` `AppUpdateSection`: import `{ ChangelogButton } from "../whatsNew"`, 제목 줄을

```tsx
      <div className="flex items-baseline gap-2">
        <h2 className="m-0 grow text-[20px] font-light tracking-[-0.02em] text-ink">
          {tr("앱 업데이트")}
        </h2>
        <ChangelogButton />
      </div>
```

로 바꾼다. `AppUpdateSection`은 데스크톱이 아니면 `null`이므로, 브라우저에서도 보이게 하려면 `if (!inDesktopApp() || !state) return null;` 앞에 브라우저용 분기를 둔다:

```tsx
  if (!inDesktopApp())
    return (
      <section
        aria-label={tr("변경 내역")}
        className={`${card} flex w-full items-center gap-3 p-6`}
      >
        <h2 className="m-0 grow text-[20px] font-light tracking-[-0.02em] text-ink">
          {t("변경 내역", "What's new")}
        </h2>
        <ChangelogButton />
      </section>
    );
  if (!state) return null;
```

(훅 순서 규칙: `useState`/`useEffect` 호출 뒤에 조기 반환하므로 문제없음. `OpsSettings.tsx`에 `t` import가 없으면 추가.)

`vite.config.ts` 상단 import에 `searchForWorkspaceRoot` 추가(`import { searchForWorkspaceRoot } from "vite";`), `server` 안:

```ts
    // CHANGELOG.md lives at the repository root, outside this package (Phase 19).
    fs: { allow: [searchForWorkspaceRoot(process.cwd()), ".."] },
```

- [ ] **Step 5: Run checks**

Run: `cd frontend && bunx vitest run && bunx tsc -b && bunx biome check src && bun run build`
Expected: PASS, 빌드 성공.

- [ ] **Step 6: 화면 확인**

Orca 도구가 있으면 별도 포트 스택(CLAUDE.md "테스트")에서 설정 → 운영 "변경 내역" 버튼을 눌러 모달을 라이트·다크로 확인하고, 개발 서버에서 CHANGELOG가 읽히는지(빈 화면 아님) 본다. 없으면 보고에 "화면 확인 못 함".

- [ ] **Step 7: Commit**

```bash
/usr/bin/git add frontend/src/whatsNew.tsx frontend/src/whatsNew.test.tsx frontend/src/layout/Shell.tsx frontend/src/pages/OpsSettings.tsx frontend/vite.config.ts frontend/src/i18n.ts
/usr/bin/git commit -m "feat: show what's new after an update"
```

---

### Task 4: 릴리스 연결과 문서

**Files:**
- Modify: `Makefile` (`release:` 타깃)
- Modify: `.github/workflows/release.yml:47-68`
- Modify: `docs/PLAN.md`, `docs/decisions.md`, `CLAUDE.md`

- [ ] **Step 1: Makefile**

`release:`의 `git status` 검사 다음 줄에:

```make
	@grep -q "^## $(VERSION) · " CHANGELOG.md || { echo "CHANGELOG.md has no '## $(VERSION) · YYYY-MM-DD' section"; exit 1; }
```

확인: `make release VERSION=9.9.9` → 커밋 안 된 변경이 없을 때 위 메시지로 멈춰야 한다(작업 트리가 깨끗해야 grep까지 감; 깨끗하지 않으면 앞 검사에서 멈추는 것도 정상). 실제 릴리스는 하지 않는다.

- [ ] **Step 2: release.yml**

`Collect files and latest.json`의 Python 블록에서 manifest 앞에:

```python
          import re
          text = open("CHANGELOG.md", encoding="utf-8").read()
          match = re.search(rf"^## {re.escape(version)} · .*?\n(.*?)(?=^## |\Z)", text, re.S | re.M)
          if not match:
              raise SystemExit(f"CHANGELOG.md has no section for {version}")
          notes = match.group(1).strip()
          open("release-notes.md", "w", encoding="utf-8").write(notes + "\n")
```

`"notes": f"Argos {version}",` → `"notes": notes,`. Publish 단계: `--generate-notes` → `--notes-file release-notes.md` (`out/*` 밖이라 자산으로 올라가지 않음).

로컬 확인:

```bash
VERSION=0.1.16 python3 - <<'PY'
import os, re
version = os.environ["VERSION"]
text = open("CHANGELOG.md", encoding="utf-8").read()
match = re.search(rf"^## {re.escape(version)} · .*?\n(.*?)(?=^## |\Z)", text, re.S | re.M)
print(match.group(1).strip())
PY
```

Expected: 0.1.16 항목만 출력.

- [ ] **Step 3: 문서**

PLAN.md, Phase 18 다음:

```markdown
### Phase 19. 앱 안 변경 내역 (추가 범위, 2026-10-22 사용자 요청)

**목표**: 업데이트 뒤 무엇이 바뀌었는지 앱에서 한국어로 본다.

- 원본은 저장소 루트 `CHANGELOG.md`(`## x.y.z · 날짜` + 목록), 프런트 빌드에 포함
- 업데이트 후 첫 실행에 지난번 본 버전 이후 내역을 모달로 한 번(`app_setting.changelog_seen`), 첫 설치엔 안 띄움
- 설정 → 운영에서 전체 내역. `make release`는 그 버전 섹션이 없으면 멈추고, 자동 업데이트 알림과 GitHub 릴리스 본문에 같은 섹션을 씀

**완료 기준**: 버전을 올려 업데이트하면 새 내역 모달이 한 번 뜨고 확인 후 다시 안 뜸 / 설정에서 전체 내역이 보임.
```

decisions.md:

```markdown
| 2026-10-22 | 변경 내역 원본은 루트 `CHANGELOG.md`(한국어만), 프런트가 `?raw`로 번들. 본 버전은 서버 `app_setting.changelog_seen` | 폰·데스크톱이 같은 기록을 공유. 백엔드·PyInstaller 변경 없음 |
| 2026-10-22 | 변경 내역 모달은 디자인 캔버스 없이 기존 카드·버튼·토큰으로 | 맞는 캔버스가 없음 |
| 2026-10-22 | 릴리스 노트는 `--generate-notes` 대신 CHANGELOG 섹션(`latest.json`의 `notes`도 같음) | 업데이트 알림과 GitHub 릴리스가 같은 사람이 읽는 글 |
```

CLAUDE.md 명령 절의 릴리스 문장을: `릴리스: CHANGELOG.md에 \`## x.y.z · 날짜\` 섹션을 먼저 쓰고 \`make release VERSION=x.y.z\` → 태그 → \`.github/workflows/release.yml\`(그 섹션이 릴리스 노트·업데이트 알림).`

- [ ] **Step 4: 전체 확인과 커밋**

Run: 루트에서 `make lint && make test`
Expected: PASS.

```bash
/usr/bin/git add Makefile .github/workflows/release.yml docs/PLAN.md docs/decisions.md CLAUDE.md
/usr/bin/git commit -m "chore: release notes come from the changelog"
```
