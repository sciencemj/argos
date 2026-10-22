import { useEffect, useRef, useState } from "react";
import { useChangelogSeen, useSaveChangelogSeen } from "./api";
import { RELEASES, type Release, releasesAfter } from "./changelog";
import { inDesktopApp, updateStatus } from "./desktop";
import { t, tt } from "./i18n";
import { Markdown } from "./markdown";
import { btn, Dialog } from "./ui";

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
  return (
    <Dialog open onClose={onClose} title={title}>
      <div className="flex flex-col gap-4">
        <div className="max-h-[60vh] overflow-y-auto">
          <ChangelogList releases={releases} />
        </div>
        <div className="flex justify-end">
          <button type="button" className={btn.cta} onClick={onClose}>
            {t("확인", "OK")}
          </button>
        </div>
      </div>
    </Dialog>
  );
}

/** After an update, the notes since the version last seen, once (Phase 19). */
export function WhatsNew({ releases = RELEASES }: { releases?: Release[] }) {
  const seen = useChangelogSeen();
  const save = useSaveChangelogSeen();
  const [current, setCurrent] = useState<string | null>(null);
  const [open, setOpen] = useState<Release[] | null>(null);
  // Decided once per session: shown, dismissed, or nothing new.
  const decided = useRef(false);

  useEffect(() => {
    if (!inDesktopApp()) return;
    void updateStatus()?.then((s) => s && setCurrent(s.current));
  }, []);

  useEffect(() => {
    if (!current || !seen.data || decided.current) return;
    decided.current = true;
    const last = seen.data.seen;
    if (!last) {
      save.mutate(current); // a new install has nothing to catch up on
      return;
    }
    const fresh = releasesAfter(releases, last, current);
    if (fresh.length) setOpen(fresh);
  }, [current, seen.data, releases, save]);

  if (!open?.length || !current) return null;
  return (
    <ChangelogDialog
      releases={open}
      title={tt`Argos ${current}에서 바뀐 점`}
      onClose={() => {
        save.mutate(current);
        setOpen(null);
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
