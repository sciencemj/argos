import { useState } from "react";
import {
  useConnectLms,
  useDisconnectLms,
  useLmsCourseNames,
  useLmsStatus,
  usePrepareLmsExtension,
} from "../api";
import { fmt } from "../dates";
import { inDesktopApp, openLmsExtension } from "../desktop";
import { tr } from "../i18n";
import { btn, card, ErrorText, field, label } from "../ui";

export function LmsSection() {
  const status = useLmsStatus();
  const connect = useConnectLms();
  const disconnect = useDisconnectLms();
  const prepare = usePrepareLmsExtension();
  const courseNames = useLmsCourseNames();
  const [token, setToken] = useState("");
  const [guide, setGuide] = useState(false);
  const [actionError, setActionError] = useState<unknown>(null);
  const [copied, setCopied] = useState("");
  const extension = prepare.data ?? status.data;
  async function copy(value: string, name: string) {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(name);
      setActionError(null);
    } catch {
      setActionError(
        new Error(
          tr(
            "복사하지 못했어요. 아래 내용을 직접 복사해 주세요.",
            "Copy failed. Please copy the text below manually.",
          ),
        ),
      );
    }
  }
  async function open(browser: boolean) {
    try {
      await openLmsExtension(browser);
      setActionError(null);
    } catch (error) {
      setActionError(new Error(String(error)));
    }
  }
  return (
    <section
      className={`${card} flex w-full flex-col gap-4 p-6`}
      aria-labelledby="lms-title"
    >
      <h3 id="lms-title" className="m-0 text-base font-semibold">
        {tr("LearningX LMS")}
      </h3>
      <p className="text-sm text-text-3">
        {tr(
          "Chrome 확장 프로그램에서 로그인된 고려대 LMS의 과제·공지·메시지 요약을 가져와요. 출결은 현재 화면의 표를 가져와요.",
        )}
      </p>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          className={btn.outline}
          disabled={prepare.isPending}
          onClick={() =>
            prepare.mutate(undefined, { onSuccess: () => setGuide(true) })
          }
        >
          {prepare.isPending
            ? tr("확장 파일 준비 중…", "Preparing extension…")
            : tr("확장 프로그램 설치 도우미", "Extension installation guide")}
        </button>
        {status.data?.extension_version && (
          <span className="self-center text-xs text-meta">
            {tr("준비된 버전", "Prepared version")}{" "}
            {status.data.extension_version}
          </span>
        )}
      </div>
      {guide && extension?.extension_path && (
        <div className="flex flex-col gap-3 rounded-lg border border-line p-4 text-sm">
          <p className="m-0">
            {tr(
              "스토어 등록 없이 설치해요. 처음 한 번만 Chrome에서 폴더를 불러와 주세요.",
              "Install without the store. Load this folder in Chrome once.",
            )}
          </p>
          <ol className="m-0 list-decimal space-y-2 pl-5">
            <li>
              {tr(
                "Chrome 주소창에 아래 주소를 입력해 확장 프로그램 화면을 여세요.",
                "Enter this address in Chrome to open the extensions page.",
              )}
              <code className="block select-all">chrome://extensions</code>
            </li>
            <li>
              {tr(
                "오른쪽 위 개발자 모드를 켜세요.",
                "Turn on Developer mode at the top right.",
              )}
            </li>
            <li>
              {tr(
                "압축해제된 확장 프로그램을 로드 → 아래 폴더를 선택하세요. Mac 폴더 선택창에서 ⌘⇧G를 누르면 경로를 붙여 넣을 수 있어요.",
                "Choose Load unpacked and select the folder below. On Mac, press ⌘⇧G in the folder picker to paste its path.",
              )}
            </li>
          </ol>
          <input
            aria-label={tr("확장 프로그램 폴더", "Extension folder")}
            className={field}
            value={extension.extension_path}
            readOnly
            onFocus={(event) => event.currentTarget.select()}
          />
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className={btn.outline}
              onClick={() =>
                void copy(extension.extension_path ?? "", "folder")
              }
            >
              {tr("폴더 경로 복사", "Copy folder path")}
            </button>
            <button
              type="button"
              className={btn.outline}
              onClick={() => void copy("chrome://extensions", "chrome")}
            >
              {tr("Chrome 주소 복사", "Copy Chrome address")}
            </button>
            {inDesktopApp() && (
              <>
                <button
                  type="button"
                  className={btn.outline}
                  onClick={() => void open(false)}
                >
                  {tr("폴더 열기", "Open folder")}
                </button>
                <button
                  type="button"
                  className={btn.outline}
                  onClick={() => void open(true)}
                >
                  {tr("Chrome 확장 관리 열기", "Open Chrome extensions")}
                </button>
              </>
            )}
          </div>
          <p className="m-0 text-xs text-meta">
            {tr(
              "이 폴더는 삭제하거나 옮기지 마세요. Argos 업데이트 후 파일을 자동 갱신해요. Chrome과 Argos가 켜져 있으면 약 5분마다 새 코드를 확인해요. 권한이 바뀌면 아래 안내를 확인해 주세요.",
              "Keep this folder in place. Argos updates its files after an app update. With Chrome and Argos running, the extension checks for new code about every five minutes. Permission changes require the instructions below.",
            )}
          </p>
          <p className="m-0">
            {tr(
              "설치 후 아래 연결 코드를 확장 팝업에 저장하세요. 주차학습 자료 저장과 자동 수집 여부도 팝업에서 선택할 수 있어요.",
              "After installing, save the connection code below in the extension popup. Choose weekly material downloads and automatic collection there too.",
            )}
          </p>
        </div>
      )}
      {status.data?.extension_seen_at && (
        <p role="status" className="m-0 text-sm text-text-3">
          {tr("확장 프로그램 연결 확인", "Extension connection confirmed")} ·{" "}
          {fmt(status.data.extension_seen_at, "yyyy-MM-dd HH:mm")}
        </p>
      )}
      {status.data?.extension_manual_update && (
        <p role="status" className="m-0 text-sm text-text-3">
          {tr(
            "확장 프로그램 권한 구성이 바뀌었어요. Chrome 확장 관리에서 새로고침하고 권한 안내를 확인해 주세요. 계속 연결되지 않으면 설치 도우미의 폴더를 다시 불러와 주세요.",
            "Extension permissions changed. Reload it in Chrome extensions and review any permission prompts. If it does not reconnect, load the installation guide's folder again.",
          )}
        </p>
      )}
      {copied && (
        <span role="status" className="text-xs text-meta">
          {tr("복사했어요", "Copied")}
        </span>
      )}
      <p className="text-sm text-text-3">
        {status.data?.connected
          ? tr("연결 코드가 발급되어 있어요")
          : tr("LMS 연결 전이에요")}
        {status.data?.last_sync &&
          ` · ${fmt(status.data.last_sync, "yyyy-MM-dd HH:mm")}`}
      </p>
      <div className="flex gap-2">
        <button
          type="button"
          className={btn.cta}
          disabled={connect.isPending || disconnect.isPending}
          onClick={() =>
            connect.mutate(undefined, {
              onSuccess: (data) => setToken(data.token),
            })
          }
        >
          {status.data?.connected
            ? tr("연결 코드 재발급")
            : tr("연결 코드 발급")}
        </button>
        {status.data?.connected && (
          <button
            type="button"
            className={btn.outline}
            disabled={connect.isPending || disconnect.isPending}
            onClick={() =>
              disconnect.mutate(undefined, {
                onSuccess: () => {
                  setToken("");
                  connect.reset();
                },
              })
            }
          >
            {tr("연결 해제")}
          </button>
        )}
      </div>
      {token && (
        <label className={label}>
          {tr("확장 프로그램에 입력할 연결 코드")}
          <input
            className={field}
            readOnly
            value={token}
            onFocus={(event) => event.currentTarget.select()}
          />
          <button
            type="button"
            className={btn.outline}
            onClick={() => void copy(token, "token")}
          >
            {tr("연결 코드 복사", "Copy connection code")}
          </button>
        </label>
      )}
      <p className="text-xs text-meta">
        {tr(
          "연결 코드를 재발급하면 기존 코드는 폐기돼요. 해제해도 가져온 학습 정보는 남아요.",
        )}
      </p>
      <div className="flex flex-col gap-2 border-t border-line-soft pt-4">
        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-1 accent-[var(--ink)]"
            checked={status.data?.clean_course_names ?? true}
            disabled={!status.data || courseNames.isPending}
            onChange={(event) => courseNames.mutate(event.target.checked)}
          />
          <span>
            {tr("과목 이름 정리", "Tidy course names")}
            <span className="block text-xs text-meta">
              {tr(
                "학기 코드·캠퍼스·영문 이름·분반 같은 부가 정보를 빼고 채널 이름을 지어요. 직접 바꾼 채널 이름은 그대로 둬요.",
                "Names channels without term codes, campus, translated titles or section numbers. Channels you renamed keep their names.",
              )}
            </span>
          </span>
        </label>
        {!!status.data?.courses?.length && (
          <ul className="m-0 flex list-none flex-col gap-1 p-0 text-xs">
            {status.data.courses.map((course) => (
              <li key={course.channel_id} className="flex flex-wrap gap-x-2">
                <span className="text-meta">{course.original}</span>
                <span className="text-meta" aria-hidden>
                  →
                </span>
                <span>{course.name}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
      <ErrorText
        error={
          actionError ??
          status.error ??
          prepare.error ??
          connect.error ??
          disconnect.error ??
          courseNames.error
        }
      />
    </section>
  );
}
