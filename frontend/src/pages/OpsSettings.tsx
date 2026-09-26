import { useEffect, useState } from "react";
import {
  useBackupKeep,
  useBackupNow,
  useInstallService,
  useNotifySettings,
  useNotifyTargets,
  useOps,
  usePrepareUninstall,
  useSaveNotify,
  useTestNotify,
  useUninstallService,
} from "../api";
import { fmt } from "../dates";
import {
  checkUpdate,
  inDesktopApp,
  restartToUpdate,
  type UpdateState,
  uninstallApp,
  updateStatus,
} from "../desktop";
import { btn, card, Dialog, ErrorText, field, label } from "../ui";

const WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"];
const HOURS = Array.from({ length: 24 }, (_, h) => h);

/** Notices and the weekly review (where they go, when). PLAN Phase 11. */
export function NotifySection() {
  const current = useNotifySettings();
  const save = useSaveNotify();
  const test = useTestNotify();
  const targets = useNotifyTargets(Boolean(current.data?.hermes_available));
  const [permission, setPermission] = useState(
    "Notification" in window ? Notification.permission : "unsupported",
  );
  const data = current.data;
  if (!data) return null;

  const change = (patch: Partial<typeof data>) =>
    save.mutate({
      hermes_target:
        patch.hermes_target !== undefined
          ? patch.hermes_target
          : data.hermes_target,
      weekly_review_weekday:
        patch.weekly_review_weekday ?? data.weekly_review_weekday,
      weekly_review_hour: patch.weekly_review_hour ?? data.weekly_review_hour,
    });
  const known = targets.data?.some((t) => t.target === data.hermes_target);

  return (
    <section
      aria-label="알림"
      className={`${card} flex w-full flex-col gap-4 p-6`}
    >
      <h2 className="m-0 text-[20px] font-light tracking-[-0.02em] text-ink">
        알림
      </h2>
      <p className="m-0 text-[13px] leading-relaxed text-text-3">
        마감 D-3·D-1·지남, 오래 둔 인박스(매일 {data.digest_hour}시 이후 한 번),
        날짜 없는 할 일을 알려 드려요. 사이드바 "알림"에 모이고, 켜 두면
        메신저로도 보내요.
      </p>
      <div className="flex flex-col gap-1">
        <label htmlFor="notify-target" className={label}>
          메신저로 보내기 (Hermes)
        </label>
        {data.hermes_available ? (
          <div className="flex items-center gap-2">
            <select
              id="notify-target"
              className={field}
              value={data.hermes_target ?? ""}
              disabled={save.isPending}
              onChange={(e) =>
                change({ hermes_target: e.target.value || null })
              }
            >
              <option value="">보내지 않음 (앱 안에서만)</option>
              {targets.data?.map((t) => (
                <option key={t.target} value={t.target}>
                  {t.label}
                </option>
              ))}
              {data.hermes_target && !known && (
                <option value={data.hermes_target}>{data.hermes_target}</option>
              )}
            </select>
            <button
              type="button"
              className={`${btn.outline} shrink-0`}
              disabled={!data.hermes_target || test.isPending}
              onClick={() =>
                data.hermes_target && test.mutate(data.hermes_target)
              }
            >
              {test.isPending
                ? "보내는 중…"
                : test.isSuccess
                  ? "보냈어요"
                  : "테스트"}
            </button>
          </div>
        ) : (
          <span className="text-[12.5px] text-text-3">
            Hermes가 설치되어 있지 않아 앱 안에서만 알려요.
          </span>
        )}
        {targets.isLoading && data.hermes_available && (
          <span className="text-[11.5px] text-meta">
            Hermes에 연결된 채널을 불러오는 중…
          </span>
        )}
      </div>
      <div className="flex flex-col gap-1">
        <span className={label}>브라우저 알림 (창이 뒤에 있을 때)</span>
        {permission === "granted" ? (
          <span className="text-[12.5px] text-text-3">켜져 있어요.</span>
        ) : permission === "unsupported" ? (
          <span className="text-[12.5px] text-text-3">
            이 브라우저는 알림을 지원하지 않아요.
          </span>
        ) : (
          <div>
            <button
              type="button"
              className={btn.outline}
              disabled={permission === "denied"}
              onClick={() =>
                void Notification.requestPermission().then(setPermission)
              }
            >
              {permission === "denied"
                ? "브라우저 설정에서 막혀 있어요"
                : "브라우저 알림 켜기"}
            </button>
          </div>
        )}
      </div>
      <div className="flex flex-col gap-1">
        <span className={label}>주간 리뷰 (#today로)</span>
        <div className="flex items-center gap-2 text-[13.5px] text-text">
          매주
          <select
            aria-label="리뷰 요일"
            className={`${field} w-20`}
            value={data.weekly_review_weekday}
            onChange={(e) =>
              change({ weekly_review_weekday: Number(e.target.value) })
            }
          >
            {WEEKDAYS.map((d, i) => (
              <option key={d} value={i}>
                {d}요일
              </option>
            ))}
          </select>
          <select
            aria-label="리뷰 시각"
            className={`${field} w-20`}
            value={data.weekly_review_hour}
            onChange={(e) =>
              change({ weekly_review_hour: Number(e.target.value) })
            }
          >
            {HOURS.map((h) => (
              <option key={h} value={h}>
                {h}시
              </option>
            ))}
          </select>
        </div>
      </div>
      <ErrorText error={save.error ?? test.error ?? targets.error} />
    </section>
  );
}

/** Backups and starting at login (PLAN Phase 11). */
export function OpsSection() {
  const ops = useOps();
  const backup = useBackupNow();
  const keep = useBackupKeep();
  const install = useInstallService();
  const uninstall = useUninstallService();
  const data = ops.data;
  if (!data) return null;
  const service = data.service as {
    supported: boolean;
    desktop: boolean;
    installed: boolean;
    running: boolean;
    plist: string;
    log: string;
    problem: string | null;
  };

  return (
    <section
      aria-label="운영"
      className={`${card} flex w-full flex-col gap-4 p-6`}
    >
      <h2 className="m-0 text-[20px] font-light tracking-[-0.02em] text-ink">
        백업과 자동 실행
      </h2>
      <div className="flex flex-col gap-2 rounded-2xl border border-line-soft px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="grow text-[13.5px] text-ink">
            데이터베이스 백업 (매일)
          </span>
          <button
            type="button"
            className={btn.outline}
            disabled={backup.isPending}
            onClick={() => backup.mutate(undefined)}
          >
            지금 백업
          </button>
        </div>
        <span className="text-[12px] text-text-3">
          {data.backup_last
            ? `마지막 ${fmt(data.backup_last, "M/d HH:mm")} · ${data.backup_count}개 보관 중`
            : "아직 백업이 없어요"}{" "}
          · 최근
          <input
            aria-label="보관 개수"
            type="number"
            min={1}
            max={365}
            defaultValue={data.backup_keep}
            onBlur={(e) => {
              const value = Number(e.target.value);
              if (value >= 1 && value !== data.backup_keep) keep.mutate(value);
            }}
            className="mx-1 w-12 border-0 border-b border-line bg-transparent text-center font-mono text-[12px] text-text outline-none"
          />
          개 보관
        </span>
        <span className="font-mono text-[11px] break-all text-meta">
          {data.backup_dir}
        </span>
        {data.backup_error && (
          <span className="text-[12px] text-danger">{data.backup_error}</span>
        )}
      </div>
      <div className="flex flex-col gap-2 rounded-2xl border border-line-soft px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="grow text-[13.5px] text-ink">
            {service.desktop
              ? "로그인할 때 Argos 앱 열기"
              : "로그인할 때 Argos 자동 실행"}
          </span>
          <span className="text-[12px] text-text-3">
            {!service.supported
              ? "macOS 전용"
              : service.installed
                ? service.desktop
                  ? "켜짐"
                  : service.running
                    ? "등록됨 · 실행 중"
                    : "등록됨 · 다음 로그인부터"
                : "꺼짐"}
          </span>
        </div>
        {service.desktop ? (
          <p className="m-0 text-[12px] leading-relaxed text-text-3">
            로그인하면 Argos 앱이 메뉴 막대에서 조용히 켜져요. 창을 닫아도
            서버는 계속 돌아서 아이패드 접속·동기화·알림이 이어져요. 끄려면 메뉴
            막대 아이콘의 "Argos 종료". 기록은 <code>{service.log}</code>.
          </p>
        ) : (
          <p className="m-0 text-[12px] leading-relaxed text-text-3">
            macOS의 launchd에 등록해 로그인하면 서버와 화면(make dev)을 켜고,
            꺼지면 다시 켜요. 설정 파일은 <code>{service.plist}</code>, 기록은{" "}
            <code>{service.log}</code>. 터미널에서 이미 켜 둔 Argos가 있으면
            그걸 끈 뒤 "지금 시작"을 누르세요.
          </p>
        )}
        {service.problem && (
          <span className="text-[12px] text-danger">{service.problem}</span>
        )}
        {service.supported && (
          <div className="flex gap-2">
            {service.installed ? (
              <>
                {!service.running && !service.desktop && (
                  <button
                    type="button"
                    className={btn.outline}
                    disabled={install.isPending}
                    onClick={() => install.mutate(true)}
                  >
                    지금 시작
                  </button>
                )}
                <button
                  type="button"
                  className={btn.ghost}
                  disabled={uninstall.isPending}
                  onClick={() =>
                    (service.desktop ||
                      confirm(
                        "자동 실행을 끌까요? 지금 launchd로 실행 중이면 멈춰요.",
                      )) &&
                    uninstall.mutate(undefined)
                  }
                >
                  자동 실행 끄기
                </button>
              </>
            ) : (
              <button
                type="button"
                className={btn.outline}
                disabled={install.isPending}
                onClick={() => install.mutate(false)}
              >
                자동 실행 켜기
              </button>
            )}
          </div>
        )}
      </div>
      <ErrorText
        error={backup.error ?? keep.error ?? install.error ?? uninstall.error}
      />
    </section>
  );
}

/** The desktop app's version and automatic updates (PLAN Phase 12). */
export function AppUpdateSection() {
  const [state, setState] = useState<UpdateState | null>(null);
  useEffect(() => {
    if (!inDesktopApp()) return;
    const read = () => void updateStatus()?.then((s) => s && setState(s));
    read();
    const id = setInterval(read, 5000); // follow a download started in the background
    return () => clearInterval(id);
  }, []);
  if (!inDesktopApp() || !state) return null;

  const status = state.checking
    ? "확인하는 중…"
    : state.ready
      ? `${state.available} 설치됨 · 다시 시작하면 적용돼요`
      : state.available
        ? `${state.available} 받는 중…`
        : state.error
          ? state.error
          : state.checked_at
            ? `최신 버전이에요 · ${fmt(new Date(state.checked_at * 1000), "M/d HH:mm")} 확인`
            : "곧 확인해요";

  return (
    <section
      aria-label="앱 업데이트"
      className={`${card} flex w-full flex-col gap-3 p-6`}
    >
      <h2 className="m-0 text-[20px] font-light tracking-[-0.02em] text-ink">
        앱 업데이트
      </h2>
      <p className="m-0 text-[13px] leading-relaxed text-text-3">
        새 버전이 나오면 알아서 받아 설치해 두고, 다음에 다시 시작할 때
        적용해요(6시간마다 확인). 데이터는 그대로이고, 업데이트 전에 DB를
        백업해요.
      </p>
      <div className="flex flex-wrap items-center gap-2 rounded-2xl border border-line-soft px-4 py-3">
        <span className="font-mono text-[13px] text-ink">v{state.current}</span>
        <span
          className={`grow text-[12.5px] ${state.error && !state.checking ? "text-danger" : "text-text-3"}`}
        >
          {status}
        </span>
        {state.ready ? (
          <button
            type="button"
            className={btn.cta}
            onClick={() => void restartToUpdate()}
          >
            다시 시작해 업데이트
          </button>
        ) : (
          <button
            type="button"
            className={btn.outline}
            disabled={state.checking}
            onClick={() => {
              setState({ ...state, checking: true });
              void checkUpdate()?.then((s) => s && setState(s));
            }}
          >
            지금 확인
          </button>
        )}
      </div>
    </section>
  );
}

/** "Argos 완전 삭제" (desktop app): everything Argos put on this Mac, in one place. */
export function UninstallSection() {
  const [open, setOpen] = useState(false);
  const [deleteData, setDeleteData] = useState(true);
  const [failed, setFailed] = useState<string | null>(null);
  const prepare = usePrepareUninstall();
  if (!inDesktopApp()) return null;

  const run = () =>
    prepare.mutate(undefined, {
      onSuccess: () =>
        void uninstallApp(deleteData)?.then(
          (error) => error && setFailed(error),
        ),
    });

  return (
    <section
      aria-label="Argos 삭제"
      className={`${card} flex w-full flex-col gap-3 p-6`}
    >
      <h2 className="m-0 text-[20px] font-light tracking-[-0.02em] text-ink">
        Argos 삭제
      </h2>
      <p className="m-0 text-[13px] leading-relaxed text-text-3">
        앱만 휴지통에 버리면 에이전트에 등록한 MCP·스킬, 로그인 시 자동 실행,
        키체인 암호가 남아요. 여기서 지우면 그것까지 한 번에 정리해요.
      </p>
      <div>
        <button
          type="button"
          className={btn.danger}
          onClick={() => setOpen(true)}
        >
          Argos 완전 삭제…
        </button>
      </div>
      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        title="Argos 완전 삭제"
      >
        <div className="flex flex-col gap-4 text-[13.5px] leading-relaxed text-text">
          <ul className="m-0 flex flex-col gap-1 pl-5 text-text-3">
            <li>Claude Code·Codex·Hermes에서 Argos MCP와 argos 스킬 제거</li>
            <li>로그인 시 자동 실행 해제, 키체인의 iCloud 앱 암호 삭제</li>
            <li>앱과 캐시를 휴지통으로 옮기고 종료</li>
          </ul>
          <label className="flex items-start gap-2">
            <input
              type="checkbox"
              checked={deleteData}
              onChange={(e) => setDeleteData(e.target.checked)}
              className="mt-1 accent-[var(--ink)]"
            />
            <span>
              내 데이터도 휴지통으로 (일정·할 일·메모·대화·백업)
              <span className="block text-[12px] text-meta">
                끄면 다시 설치했을 때 그대로 이어 쓸 수 있어요.
              </span>
            </span>
          </label>
          <p className="m-0 text-[12px] text-meta">
            휴지통을 비우기 전까지는 되돌릴 수 있어요.
          </p>
          <ErrorText error={prepare.error} />
          {failed && (
            <p className="m-0 text-[12px] whitespace-pre-wrap text-danger">
              일부를 옮기지 못했어요: {failed}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <button
              type="button"
              className={btn.ghost}
              onClick={() => setOpen(false)}
            >
              취소
            </button>
            <button
              type="button"
              className={btn.danger}
              disabled={prepare.isPending}
              onClick={run}
            >
              {prepare.isPending ? "정리하는 중…" : "삭제"}
            </button>
          </div>
        </div>
      </Dialog>
    </section>
  );
}
