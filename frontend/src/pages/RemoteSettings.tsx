import QRCode from "qrcode";
import { type FormEvent, useEffect, useState } from "react";
import { useRemote, useRemoteSettings, useRemoteSwitch } from "../api";
import { tr, tt } from "../i18n";
import { CloseIcon } from "../icons";
import { btn, card, ErrorText, field } from "../ui";

/** Phone access through Tailscale serve (PLAN Phase 14): on/off, the address and its QR
 * code, the accounts let in, and serving again whenever the app starts. */
export function RemoteSection() {
  const remote = useRemote();
  const flip = useRemoteSwitch();
  const save = useRemoteSettings();
  const [copied, setCopied] = useState(false);
  const data = remote.data;
  // On only when serve points here and someone is let in (an empty list lets nobody in).
  const on = Boolean(
    data?.enabled && data.proxy === data.target && data.allowed.length > 0,
  );

  return (
    <section
      aria-label={tr("폰에서 열기")}
      className={`${card} flex w-full flex-col gap-4 p-6`}
    >
      <div className="flex items-center gap-3">
        <h2 className="m-0 grow text-[20px] font-light tracking-[-0.02em] text-ink">
          {tr("폰에서 열기")}
        </h2>
        {on && (
          <span className="rounded-full bg-inset px-2.5 py-0.5 text-[12px] font-medium text-text">
            {tr("켜짐")}
          </span>
        )}
      </div>
      <p className="m-0 text-[13px] leading-relaxed text-text-3">
        {tr(
          "Tailscale로 폰·아이패드에서 Argos를 열어요. 이 Mac과 같은 Tailscale 계정(또는 아래에 추가한 계정)으로 로그인한 기기만 들어올 수 있고, 주소는 같은 tailnet 밖에서는 열리지 않아요.",
        )}
      </p>

      {data && (
        <div className="flex flex-col gap-1 rounded-2xl border border-line-soft px-4 py-3 text-[12.5px]">
          <span className="text-text-2">
            {data.login
              ? tt`Tailscale · ${data.login}${data.host ? ` · ${data.host}` : ""}`
              : tr("Tailscale")}
          </span>
          {data.problem && (
            <span className="text-danger [overflow-wrap:anywhere]">
              {tr(data.problem)}
            </span>
          )}
          {!data.installed && (
            <a
              href="https://tailscale.com/download/mac"
              target="_blank"
              rel="noreferrer"
              className="text-text-2 underline"
            >
              {tr("Tailscale 내려받기")}
            </a>
          )}
          {data.enable_link && (
            <a
              href={data.enable_link}
              target="_blank"
              rel="noreferrer"
              className="font-mono text-[12px] break-all text-text-2 underline"
            >
              {data.enable_link}
            </a>
          )}
          {data.proxy && data.proxy !== data.target && (
            <span className="text-text-3 [overflow-wrap:anywhere]">
              {tt`지금 이 주소는 다른 곳(${data.proxy})을 가리켜요. 켜기를 누르면 이 Argos로 바꿔요.`}
            </span>
          )}
        </div>
      )}

      <ErrorText error={remote.error ?? flip.error} />
      <div className="flex flex-wrap items-center gap-2">
        {on ? (
          <button
            type="button"
            className={btn.outline}
            disabled={flip.isPending}
            onClick={() => flip.mutate(false)}
          >
            {tr("끄기")}
          </button>
        ) : (
          <button
            type="button"
            className={btn.cta}
            disabled={!data?.running || flip.isPending}
            onClick={() => flip.mutate(true)}
          >
            {flip.isPending ? tr("켜는 중…") : tr("켜기")}
          </button>
        )}
        <button
          type="button"
          className={btn.ghost}
          disabled={remote.isFetching}
          onClick={() => void remote.refetch()}
        >
          {tr("다시 확인")}
        </button>
      </div>

      {on && data?.url && (
        <div className="flex flex-wrap items-center gap-5 rounded-2xl border border-line-soft px-4 py-4">
          <QrCode text={data.url} />
          <div className="flex min-w-0 grow basis-56 flex-col gap-2">
            <span className="font-mono text-[12.5px] break-all text-ink">
              {data.url}
            </span>
            <div>
              <button
                type="button"
                className={btn.outline}
                onClick={() =>
                  void navigator.clipboard
                    .writeText(data.url ?? "")
                    .then(() => setCopied(true))
                }
              >
                {copied ? tr("복사했어요") : tr("복사")}
              </button>
            </div>
            <span className="text-[12px] leading-relaxed text-meta">
              {tr(
                "폰에서 Tailscale을 켜고 카메라로 QR을 찍거나 주소를 Safari로 여세요. 공유 → 홈 화면에 추가하면 앱처럼 열려요.",
              )}
            </span>
          </div>
        </div>
      )}

      {data && (
        <>
          <label className="flex items-start gap-2.5 text-[13px] text-text-2">
            <input
              type="checkbox"
              checked={data.autostart}
              disabled={save.isPending}
              onChange={(e) =>
                save.mutate({
                  allowed: data.allowed,
                  autostart: e.target.checked,
                })
              }
              className="mt-1 accent-[var(--ink)]"
            />
            <span>
              {tr("앱을 켤 때 폰에서 열기도 켜기")}
              <span className="block text-[12px] text-meta">
                {tr(
                  "Argos가 시작할 때마다 Tailscale 주소를 다시 걸어요. 끄기를 눌러도 이 설정은 그대로예요.",
                )}
              </span>
            </span>
          </label>
          <AllowedAccounts
            allowed={data.allowed}
            pending={save.isPending}
            onChange={(allowed) =>
              save.mutate({ allowed, autostart: data.autostart })
            }
          />
          <ErrorText error={save.error} />
        </>
      )}
    </section>
  );
}

function AllowedAccounts({
  allowed,
  pending,
  onChange,
}: {
  allowed: string[];
  pending: boolean;
  onChange: (allowed: string[]) => void;
}) {
  const [draft, setDraft] = useState("");
  const add = (e: FormEvent) => {
    e.preventDefault();
    const login = draft.trim().toLowerCase();
    if (!login) return;
    onChange([...allowed, login]);
    setDraft("");
  };
  return (
    <div className="flex flex-col gap-2">
      <span className="text-[12.5px] font-medium text-text-2">
        {tr("들어올 수 있는 Tailscale 계정")}
      </span>
      {allowed.length === 0 ? (
        <span className="text-[12px] text-meta">
          {tr("켜기를 누르면 이 Mac의 Tailscale 계정이 들어가요.")}
        </span>
      ) : (
        <ul className="m-0 flex list-none flex-wrap gap-1.5 p-0">
          {allowed.map((login) => (
            <li
              key={login}
              className="flex items-center gap-1 rounded-full bg-inset py-0.5 pr-1 pl-3 font-mono text-[12px] text-text"
            >
              {login}
              <button
                type="button"
                aria-label={tt`${login} 빼기`}
                disabled={pending}
                onClick={() => onChange(allowed.filter((a) => a !== login))}
                className="flex size-6 cursor-pointer items-center justify-center rounded-full text-meta hover:text-ink"
              >
                <CloseIcon size={12} />
              </button>
            </li>
          ))}
        </ul>
      )}
      <form onSubmit={add} className="flex gap-2">
        <input
          className={`${field} min-w-0 grow`}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="name@example.com"
          aria-label={tr("추가할 Tailscale 계정")}
          autoCapitalize="none"
          autoCorrect="off"
        />
        <button
          type="submit"
          className={`${btn.outline} shrink-0`}
          disabled={pending || !draft.trim()}
        >
          {tr("추가")}
        </button>
      </form>
    </div>
  );
}

/** The phone address as a QR code. Always dark on light, whatever the theme: phone
 * cameras read that most reliably. */
function QrCode({ text }: { text: string }) {
  const [svg, setSvg] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    QRCode.toString(text, { type: "svg", margin: 2, errorCorrectionLevel: "M" })
      .then((out) => live && setSvg(out))
      .catch(() => live && setSvg(null));
    return () => {
      live = false;
    };
  }, [text]);
  if (!svg) return null;
  return (
    <img
      alt={tr("폰에서 열 주소 QR 코드")}
      src={`data:image/svg+xml;utf8,${encodeURIComponent(svg)}`}
      className="size-36 shrink-0 rounded-xl"
    />
  );
}
