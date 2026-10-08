import { useState } from "react";
import { useConnectLms, useDisconnectLms, useLmsStatus } from "../api";
import { fmt } from "../dates";
import { tr } from "../i18n";
import { btn, card, ErrorText, field, label } from "../ui";

export function LmsSection() {
  const status = useLmsStatus();
  const connect = useConnectLms();
  const disconnect = useDisconnectLms();
  const [token, setToken] = useState("");
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
        </label>
      )}
      <p className="text-xs text-meta">
        {tr(
          "연결 코드를 재발급하면 기존 코드는 폐기돼요. 해제해도 가져온 학습 정보는 남아요.",
        )}
      </p>
      <ErrorText error={status.error ?? connect.error ?? disconnect.error} />
    </section>
  );
}
