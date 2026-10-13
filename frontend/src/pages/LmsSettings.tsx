import { useState } from "react";
import {
  useConnectLms,
  useConnectLmsToken,
  useDisconnectLms,
  useDisconnectLmsToken,
  useLmsCourseNames,
  useLmsMaterials,
  useLmsStatus,
  useLmsSummarySetting,
  useMergeLmsCourse,
  usePrepareLmsExtension,
  useSyncLmsNow,
} from "../api";
import { fmt } from "../dates";
import { inDesktopApp, openLmsExtension } from "../desktop";
import { tr } from "../i18n";
import { btn, card, ErrorText, field, label } from "../ui";

export function LmsSection() {
  const status = useLmsStatus();
  const courseNames = useLmsCourseNames();
  const merge = useMergeLmsCourse();
  const targets = status.data?.merge_targets ?? [];
  const summarySetting = useLmsSummarySetting();
  return (
    <section
      className={`${card} flex w-full flex-col gap-4 p-6`}
      aria-labelledby="lms-title"
    >
      <h3 id="lms-title" className="m-0 text-base font-semibold">
        {tr("학교 LMS", "School LMS")}
      </h3>
      <p className="m-0 text-sm text-text-3">
        {tr(
          "학교 LMS(Canvas)에서 발급한 액세스 토큰으로 과제·공지·메시지와 주차학습 자료를 15분마다 가져와요. 브라우저를 켜 두지 않아도 돼요.",
          "Imports assignments, announcements, messages and weekly materials every 15 minutes with an access token from your school's Canvas LMS. No browser needed.",
        )}
      </p>
      <TokenConnection />
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
              <li
                key={course.channel_id}
                className="flex flex-wrap items-center gap-x-2"
              >
                <span className="text-meta">{course.original}</span>
                <span className="text-meta" aria-hidden>
                  →
                </span>
                <span>{course.name}</span>
                {course.course && targets.length > 0 && (
                  <select
                    aria-label={tr(
                      `${course.name} 합칠 채널`,
                      `Merge ${course.name} into`,
                    )}
                    className="ml-auto rounded-md border border-line bg-card px-1.5 py-0.5 text-xs text-text-2"
                    value=""
                    disabled={merge.isPending}
                    onChange={(event) => {
                      const target = targets.find(
                        (t) => t.channel_id === event.target.value,
                      );
                      if (
                        target &&
                        window.confirm(
                          tr(
                            `'${course.name}'을(를) '${target.name}'에 합칠까요? 이 채널의 내용이 그쪽으로 옮겨지고 이 채널은 사라져요. 되돌릴 수 없어요.`,
                            `Merge '${course.name}' into '${target.name}'? Its contents move there and this channel goes away. This cannot be undone.`,
                          ),
                        )
                      ) {
                        merge.mutate({
                          channelId: course.channel_id,
                          targetId: target.channel_id,
                        });
                      }
                    }}
                  >
                    <option value="">
                      {tr("내 채널과 합치기…", "Merge into my channel…")}
                    </option>
                    {targets.map((t) => (
                      <option key={t.channel_id} value={t.channel_id}>
                        {t.name}
                      </option>
                    ))}
                  </select>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="flex flex-col gap-2 border-t border-line-soft pt-4">
        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-1 accent-[var(--ink)]"
            checked={(status.data?.summary_min_chars ?? 300) > 0}
            disabled={!status.data || summarySetting.isPending}
            onChange={(event) =>
              summarySetting.mutate(event.target.checked ? 300 : 0)
            }
          />
          <span>
            {tr("알림 AI 요약", "AI notice summaries")}
            <span className="block text-xs text-meta">
              {status.data?.summary_available
                ? tr(
                    "300자 이상인 LMS 알림을 인박스 분류 모델이 몇 줄로 요약해요. 원문은 카드에서 볼 수 있어요.",
                    "The Inbox classifier model sums up LMS notices of 300+ characters. The original stays on the card.",
                  )
                : tr(
                    "인박스 분류 모델을 설정하면 긴 LMS 알림을 요약해요.",
                    "Set up an Inbox classifier model to summarize long LMS notices.",
                  )}
            </span>
          </span>
        </label>
      </div>
      <ErrorText
        error={
          status.error ??
          courseNames.error ??
          merge.error ??
          summarySetting.error
        }
      />
      <details className="border-t border-line-soft pt-4 text-sm">
        <summary className="cursor-pointer text-text-3">
          {tr(
            "다른 연결 방법: 브라우저 확장 프로그램",
            "Another way: browser extension",
          )}
          {status.data?.connected &&
            ` · ${tr("연결 코드 있음", "code issued")}`}
        </summary>
        <ExtensionConnection />
      </details>
    </section>
  );
}

/** The default way in: LMS address + Canvas access token (kept in the Keychain). */
function TokenConnection() {
  const status = useLmsStatus();
  const connect = useConnectLmsToken();
  const disconnect = useDisconnectLmsToken();
  const syncNow = useSyncLmsNow();
  const materials = useLmsMaterials();
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [replacing, setReplacing] = useState(false);
  const [guide, setGuide] = useState(false);
  const data = status.data;
  const connected = !!data?.token_url;
  const busy = connect.isPending || disconnect.isPending;
  const showForm = !connected || replacing;
  return (
    <div className="flex flex-col gap-3">
      {connected && data && (
        <div className="flex flex-col gap-1 text-sm">
          <p role="status" className="m-0">
            {data.token_user
              ? tr(
                  `${data.token_user} 계정으로 연결됨`,
                  `Connected as ${data.token_user}`,
                )
              : tr("연결됨", "Connected")}
            <span className="text-text-3"> · {data.token_url}</span>
          </p>
          <p className="m-0 text-xs text-meta">
            {data.token_syncing
              ? tr("가져오는 중…", "Syncing…")
              : data.token_last_sync
                ? `${tr("마지막 동기화", "Last sync")} ${fmt(data.token_last_sync, "yyyy-MM-dd HH:mm")}`
                : tr("곧 처음 가져와요", "First sync starts shortly")}
          </p>
          {data.token_error && (
            <p role="alert" className="m-0 text-[12.5px] text-danger">
              {data.token_error}
            </p>
          )}
          {!!data.token_warnings?.length && (
            <details className="text-xs text-text-3">
              <summary className="cursor-pointer">
                {tr(
                  `일부 항목을 가져오지 못했어요 (${data.token_warnings.length})`,
                  `Some items could not be imported (${data.token_warnings.length})`,
                )}
              </summary>
              <ul className="m-0 mt-1 list-disc pl-5">
                {data.token_warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
      {connected && (
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className={btn.cta}
            disabled={busy || syncNow.isPending || !!data?.token_syncing}
            onClick={() => syncNow.mutate()}
          >
            {syncNow.isPending || data?.token_syncing
              ? tr("가져오는 중…", "Syncing…")
              : tr("지금 가져오기", "Sync now")}
          </button>
          <button
            type="button"
            className={btn.outline}
            disabled={busy}
            onClick={() => {
              setUrl(data?.token_url ?? "");
              setReplacing(!replacing);
            }}
          >
            {tr("토큰 바꾸기", "Replace token")}
          </button>
          <button
            type="button"
            className={btn.outline}
            disabled={busy}
            onClick={() =>
              disconnect.mutate(undefined, {
                onSuccess: () => setReplacing(false),
              })
            }
          >
            {tr("연결 해제", "Disconnect")}
          </button>
        </div>
      )}
      {showForm && (
        <form
          className="flex flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            connect.mutate(
              { url: url.trim(), token: token.trim() },
              {
                onSuccess: () => {
                  setToken("");
                  setReplacing(false);
                },
              },
            );
          }}
        >
          <label className={label}>
            {tr("LMS 주소", "LMS address")}
            <input
              className={field}
              value={url}
              placeholder={tr(
                "예: https://lms.school.ac.kr",
                "e.g. https://canvas.school.edu",
              )}
              autoComplete="url"
              onChange={(event) => setUrl(event.target.value)}
            />
          </label>
          <label className={label}>
            {tr("액세스 토큰", "Access token")}
            <input
              className={field}
              type="password"
              value={token}
              autoComplete="off"
              spellCheck={false}
              onChange={(event) => setToken(event.target.value)}
            />
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="submit"
              className={btn.cta}
              disabled={busy || !url.trim() || !token.trim()}
            >
              {connect.isPending
                ? tr("확인하는 중…", "Checking…")
                : tr("연결", "Connect")}
            </button>
            <button
              type="button"
              className={btn.ghost}
              aria-expanded={guide}
              onClick={() => setGuide(!guide)}
            >
              {tr("토큰 발급 방법", "How to get a token")}
            </button>
          </div>
          {guide && (
            <ol className="m-0 list-decimal space-y-1 rounded-lg border border-line p-4 pl-8 text-sm">
              <li>
                {tr(
                  "LMS에 로그인한 뒤 왼쪽 메뉴의 계정 → 설정으로 가요.",
                  "Sign in to the LMS and open Account → Settings in the left menu.",
                )}
              </li>
              <li>
                {tr(
                  "아래쪽 승인된 통합(Approved Integrations)에서 새 액세스 토큰(New Access Token)을 눌러요.",
                  "Under Approved Integrations, choose New Access Token.",
                )}
              </li>
              <li>
                {tr(
                  "용도(예: Argos)와 만료일을 정하고 토큰을 생성해요. 학교마다 최대 기간이 정해져 있어요.",
                  "Enter a purpose (e.g. Argos) and an expiry date, then generate the token. Schools set a maximum lifetime.",
                )}
              </li>
              <li>
                {tr(
                  "만든 토큰을 복사해 위에 붙여 넣어요. 창을 닫으면 다시 볼 수 없어요.",
                  "Copy the token and paste it above. It is not shown again after you close the dialog.",
                )}
              </li>
            </ol>
          )}
        </form>
      )}
      {connected && (
        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-1 accent-[var(--ink)]"
            checked={!!data?.materials}
            disabled={materials.isPending}
            onChange={(event) => materials.mutate(event.target.checked)}
          />
          <span>
            {tr("주차학습 자료도 저장", "Also save weekly materials")}
            <span className="block text-xs text-meta">
              {tr(
                "과목 모듈의 파일(25MB까지)을 받아 과목 피드에 첨부해요. 동영상과 외부 도구는 열지 않아요.",
                "Downloads module files (up to 25 MB) and attaches them in the course feed. Videos and external tools are skipped.",
              )}
            </span>
          </span>
        </label>
      )}
      <p className="m-0 text-xs text-meta">
        {tr(
          "토큰은 이 Mac의 키체인에만 저장하고, Argos는 LMS에서 읽기만 해요. 토큰이 만료되면 새로 발급해 다시 연결해 주세요. 연결을 해제해도 가져온 학습 정보는 남아요.",
          "The token stays in this Mac's Keychain and Argos only reads from the LMS. When it expires, generate a new one and connect again. Disconnecting keeps what was imported.",
        )}
      </p>
      <ErrorText
        error={
          connect.error ?? disconnect.error ?? syncNow.error ?? materials.error
        }
      />
    </div>
  );
}

/** The alternative: a Chrome extension reading the signed-in LMS tab (Korea Univ.). */
function ExtensionConnection() {
  const status = useLmsStatus();
  const connect = useConnectLms();
  const disconnect = useDisconnectLms();
  const prepare = usePrepareLmsExtension();
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
    <div className="mt-3 flex flex-col gap-4">
      <p className="m-0 text-sm text-text-3">
        {tr(
          "토큰을 발급할 수 없을 때 쓰는 방법이에요. Chrome 확장 프로그램이 로그인된 고려대 LMS 탭에서 과제·공지·메시지 요약을 가져오고, 출결은 현재 화면의 표를 가져와요. Chrome과 LMS 탭이 열려 있어야 해요.",
          "For when you cannot create a token. A Chrome extension reads assignments, announcements and messages from a signed-in Korea University LMS tab, and the attendance table on screen. Chrome and an LMS tab must be open.",
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
      <p className="m-0 text-sm text-text-3">
        {status.data?.connected
          ? tr("연결 코드가 발급되어 있어요", "A connection code is issued")
          : tr("확장 프로그램 연결 전이에요", "Extension not connected")}
        {status.data?.last_sync &&
          ` · ${fmt(status.data.last_sync, "yyyy-MM-dd HH:mm")}`}
      </p>
      <div className="flex gap-2">
        <button
          type="button"
          className={btn.outline}
          disabled={connect.isPending || disconnect.isPending}
          onClick={() =>
            connect.mutate(undefined, {
              onSuccess: (data) => setToken(data.token),
            })
          }
        >
          {status.data?.connected
            ? tr("연결 코드 재발급", "Issue a new code")
            : tr("연결 코드 발급", "Issue a connection code")}
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
            {tr("확장 연결 해제", "Disconnect extension")}
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
      <p className="m-0 text-xs text-meta">
        {tr(
          "연결 코드를 재발급하면 기존 코드는 폐기돼요. 해제해도 가져온 학습 정보는 남아요.",
          "Issuing a new code revokes the old one. Disconnecting keeps what was imported.",
        )}
      </p>
      <ErrorText
        error={
          actionError ?? prepare.error ?? connect.error ?? disconnect.error
        }
      />
    </div>
  );
}
