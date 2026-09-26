import {
  type FormEvent,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { NavLink, useLocation, useMatch, useNavigate } from "react-router";
import { type Area, useChannels, useCreateArea, useDeleteArea } from "../api";
import { DogIcon, PlusIcon, SettingsIcon } from "../icons";
import { btn, ContextMenu, Dialog, ErrorText, field, label } from "../ui";

// The active look (card, border, shadow) is one pill that slides between buttons.
const railButton =
  "relative z-10 flex size-[42px] cursor-pointer items-center justify-center rounded-xl border border-transparent text-[15px] transition-[color,background-color,scale] duration-200 ease-out active:scale-90";
const active = "font-semibold text-ink";
const idle = "text-text-3 hover:bg-card/60 hover:text-ink";

export function Rail({
  areaId,
  onPickArea,
}: {
  areaId: string | null;
  onPickArea: (id: string | null) => void;
}) {
  const navigate = useNavigate();
  const { data } = useChannels();
  const remove = useDeleteArea();
  const [adding, setAdding] = useState(false);
  const [menu, setMenu] = useState<{ x: number; y: number; area: Area } | null>(
    null,
  );
  const [deleting, setDeleting] = useState<Area | null>(null);
  const onSettings = useMatch("/settings") !== null;
  const location = useLocation();
  // The last page outside settings, however settings were opened (rail, status bar, links).
  const lastPage = useRef("/");
  useEffect(() => {
    if (!onSettings) lastPage.current = location.pathname + location.search;
  }, [onSettings, location.pathname, location.search]);

  // Where the sliding pill sits: over whichever button is marked active.
  const nav = useRef<HTMLElement>(null);
  const [pill, setPill] = useState<{ top: number; moved: boolean } | null>(
    null,
  );
  const activeKey = onSettings ? "settings" : (areaId ?? "home");
  // biome-ignore lint/correctness/useExhaustiveDependencies: re-measure when the buttons change
  useLayoutEffect(() => {
    const place = () => {
      const el = nav.current?.querySelector<HTMLElement>("[data-active]");
      setPill((prev) =>
        el ? { top: el.offsetTop, moved: prev !== null } : null,
      );
    };
    place();
    window.addEventListener("resize", place);
    return () => window.removeEventListener("resize", place);
  }, [activeKey, data?.areas.length]);

  const openArea = (id: string) => {
    onPickArea(id);
    const first = data?.channels.find((c) => c.area_id === id);
    navigate(first ? `/c/${first.id}` : "/");
  };

  return (
    <nav
      ref={nav}
      aria-label="영역"
      data-tauri-drag-region // the desktop app's window moves by its empty parts
      className="relative flex flex-col items-center gap-2.5 border-r border-line-soft bg-rail py-3.5"
    >
      {pill && (
        <span
          aria-hidden
          className={`pointer-events-none absolute left-1/2 size-[42px] -translate-x-1/2 rounded-xl border border-line bg-card shadow-raised ${pill.moved ? "rail-pill" : ""}`}
          style={{ top: pill.top }}
        />
      )}
      <div className="flex size-[42px] shrink-0 items-center justify-center rounded-xl bg-step-5 text-on-dark">
        <DogIcon />
      </div>
      <div className="my-1 h-px w-6 bg-line" />
      <button
        type="button"
        aria-label="홈 · 오늘"
        data-active={activeKey === "home" || undefined}
        aria-current={activeKey === "home" ? "page" : undefined}
        className={`${railButton} ${activeKey === "home" ? active : idle}`}
        onClick={() => {
          onPickArea(null);
          navigate("/");
        }}
      >
        홈
      </button>
      {data?.areas.map((area) => (
        <button
          key={area.id}
          type="button"
          aria-label={area.name}
          title={`${area.name} · 우클릭하여 삭제`}
          data-active={activeKey === area.id || undefined}
          aria-current={activeKey === area.id ? "page" : undefined}
          className={`${railButton} ${activeKey === area.id ? active : idle}`}
          onClick={() => openArea(area.id)}
          onContextMenu={(event) => {
            event.preventDefault();
            setMenu({ x: event.clientX, y: event.clientY, area });
          }}
        >
          {area.icon || area.name.slice(0, 1)}
        </button>
      ))}
      <button
        type="button"
        aria-label="영역 추가"
        className={`${railButton} border-dashed !border-line text-meta hover:bg-card/60 hover:text-ink`}
        onClick={() => setAdding(true)}
      >
        <PlusIcon size={17} />
      </button>
      <span className="grow" />
      <NavLink
        to="/settings"
        // Remember where settings were opened from; pressing it again goes back there.
        state={
          onSettings ? undefined : { from: location.pathname + location.search }
        }
        aria-label={onSettings ? "설정 닫기" : "설정"}
        title={onSettings ? "설정 닫기 (이전 화면으로)" : "설정"}
        onClick={(e) => {
          if (!onSettings) return;
          e.preventDefault();
          navigate(lastPage.current); // pressed again: back to where settings were opened
        }}
        data-active={onSettings || undefined}
        className={`${railButton} ${onSettings ? active : idle}`}
      >
        <SettingsIcon size={18} />
      </NavLink>
      <AddAreaDialog open={adding} onClose={() => setAdding(false)} />
      {menu && (
        <ContextMenu
          x={menu.x}
          y={menu.y}
          label="영역 삭제…"
          onClose={() => setMenu(null)}
          onAction={() => {
            remove.reset();
            setDeleting(menu.area);
          }}
        />
      )}
      {deleting && (
        <Dialog
          open
          onClose={() => setDeleting(null)}
          title={`${deleting.name} 영역 삭제`}
        >
          <div className="flex flex-col gap-4 text-[13px] leading-relaxed text-text-2">
            <p className="m-0">
              영역을 삭제할까요? 채널과 그 안의 내용은 삭제되지 않고 전체에
              남아요.
            </p>
            <ErrorText error={remove.error} />
            <div className="flex justify-end gap-2">
              <button
                type="button"
                className={btn.ghost}
                onClick={() => setDeleting(null)}
              >
                취소
              </button>
              <button
                type="button"
                className={btn.danger}
                disabled={remove.isPending}
                onClick={() =>
                  remove.mutate(deleting.id, {
                    onSuccess: () => {
                      if (areaId === deleting.id) {
                        onPickArea(null);
                        navigate("/");
                      }
                      setDeleting(null);
                    },
                  })
                }
              >
                영역 삭제
              </button>
            </div>
          </div>
        </Dialog>
      )}
    </nav>
  );
}

function AddAreaDialog({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const create = useCreateArea();
  const [name, setName] = useState("");
  const [icon, setIcon] = useState("");

  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate(
      { name: name.trim(), icon: icon.trim() || null },
      {
        onSuccess: () => {
          setName("");
          setIcon("");
          onClose();
        },
      },
    );
  };

  return (
    <Dialog open={open} onClose={onClose} title="영역 추가">
      <form onSubmit={submit} className="flex flex-col gap-4">
        <label className="flex flex-col gap-1">
          <span className={label}>이름</span>
          <input
            className={field}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="예: 학업"
            required
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className={label}>
            레일 표시 (1~2글자, 비우면 이름 첫 글자)
          </span>
          <input
            className={field}
            value={icon}
            maxLength={2}
            onChange={(e) => setIcon(e.target.value)}
          />
        </label>
        <ErrorText error={create.error} />
        <div className="flex justify-end gap-2">
          <button type="button" className={btn.ghost} onClick={onClose}>
            취소
          </button>
          <button type="submit" className={btn.cta} disabled={create.isPending}>
            추가하기
          </button>
        </div>
      </form>
    </Dialog>
  );
}
