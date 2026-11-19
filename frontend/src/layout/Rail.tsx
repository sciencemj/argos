import {
  type FormEvent,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import {
  Link,
  NavLink,
  useLocation,
  useMatch,
  useNavigate,
} from "react-router";
import {
  type Area,
  useChannels,
  useCreateArea,
  useDeleteArea,
  useUnread,
  useUpdateArea,
} from "../api";
import { AreaIconPicker } from "../areaIcons";
import { tr, tt } from "../i18n";
import { AreaIcon, DogIcon, HomeIcon, PlusIcon, SettingsIcon } from "../icons";
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
  const unread = useUnread();
  // Areas holding an unread course/project channel: their sidebar is not on screen.
  const unreadAreas = new Set(
    data?.channels
      .filter(
        (c) =>
          (c.kind === "course" || c.kind === "project") &&
          (unread.data?.[c.id] ?? 0) > 0,
      )
      .map((c) => c.area_id),
  );
  const remove = useDeleteArea();
  const [adding, setAdding] = useState(false);
  const [menu, setMenu] = useState<{ x: number; y: number; area: Area } | null>(
    null,
  );
  const [deleting, setDeleting] = useState<Area | null>(null);
  const [editing, setEditing] = useState<Area | null>(null);
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
    // An empty area has its own page: "/" would bounce to the last channel (v0.2).
    navigate(first ? `/c/${first.id}` : `/area/${id}`);
  };

  return (
    <nav
      ref={nav}
      data-area-rail
      aria-label={tr("영역")}
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
      <Link
        to="/"
        aria-label={tr("홈 · 오늘")}
        title={tr("홈 · 오늘")}
        onClick={() => onPickArea(null)}
        className="flex size-[42px] shrink-0 items-center justify-center rounded-xl bg-step-5 text-on-dark transition-[scale] duration-200 ease-out active:scale-90"
      >
        <DogIcon />
      </Link>
      <div className="my-1 h-px w-6 bg-line" />
      <button
        type="button"
        aria-label={tr("홈 · 오늘")}
        data-active={activeKey === "home" || undefined}
        aria-current={activeKey === "home" ? "page" : undefined}
        className={`${railButton} ${activeKey === "home" ? active : idle}`}
        onClick={() => {
          onPickArea(null);
          navigate("/today");
        }}
      >
        <HomeIcon size={19} />
      </button>
      {data?.areas.map((area) => (
        <button
          key={area.id}
          type="button"
          aria-label={area.name}
          title={tt`${area.name} · 우클릭하여 편집·삭제`}
          data-active={activeKey === area.id || undefined}
          aria-current={activeKey === area.id ? "page" : undefined}
          className={`${railButton} ${activeKey === area.id ? active : idle}`}
          onClick={() => openArea(area.id)}
          onContextMenu={(event) => {
            event.preventDefault();
            setMenu({ x: event.clientX, y: event.clientY, area });
          }}
        >
          <AreaIcon
            icon={area.icon}
            fallback={area.icon || area.name.slice(0, 1)}
            size={18}
          />
          {activeKey !== area.id && unreadAreas.has(area.id) && (
            <span
              role="img"
              aria-label={tr("안 읽은 메시지")}
              className="absolute top-1 right-1 size-[7px] rounded-full bg-ink ring-2 ring-rail"
            />
          )}
        </button>
      ))}
      <button
        type="button"
        aria-label={tr("영역 추가")}
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
        aria-label={onSettings ? tr("설정 닫기") : tr("설정")}
        title={onSettings ? tr("설정 닫기 (이전 화면으로)") : tr("설정")}
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
      <AreaDialog
        open={adding}
        onClose={() => setAdding(false)}
        onCreated={(area) => openArea(area.id)}
      />
      {editing && (
        <AreaDialog
          key={editing.id}
          open
          area={editing}
          onClose={() => setEditing(null)}
        />
      )}
      {menu && (
        <ContextMenu
          x={menu.x}
          y={menu.y}
          label={tr("영역 삭제…")}
          items={[
            {
              label: tr("이름·아이콘 바꾸기…", "Rename or change icon…"),
              onAction: () => setEditing(menu.area),
            },
          ]}
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
          title={tt`${deleting.name} 영역 삭제`}
        >
          <div className="flex flex-col gap-4 text-[13px] leading-relaxed text-text-2">
            <p className="m-0">
              {tr(
                "영역을 삭제할까요? 채널과 그 안의 내용은 삭제되지 않고 전체에 남아요.",
              )}
            </p>
            <ErrorText error={remove.error} />
            <div className="flex justify-end gap-2">
              <button
                type="button"
                className={btn.ghost}
                onClick={() => setDeleting(null)}
              >
                {tr("취소")}
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
                {tr("영역 삭제")}
              </button>
            </div>
          </div>
        </Dialog>
      )}
    </nav>
  );
}

/** Add an area, or (with `area`) change its name and rail mark: letters or an icon. */
function AreaDialog({
  open,
  area,
  onClose,
  onCreated,
}: {
  open: boolean;
  area?: Area | null;
  onClose: () => void;
  onCreated?: (area: Area) => void;
}) {
  const create = useCreateArea();
  const update = useUpdateArea();
  const [name, setName] = useState(area?.name ?? "");
  const [icon, setIcon] = useState(area?.icon ?? "");
  const write = area ? update : create;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const body = { name: name.trim(), icon: icon.trim() || null };
    if (area) {
      update.mutate({ id: area.id, ...body }, { onSuccess: onClose });
      return;
    }
    create.mutate(body, {
      onSuccess: (created) => {
        setName("");
        setIcon("");
        onClose();
        onCreated?.(created);
      },
    });
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={area ? tr("영역 편집", "Edit area") : tr("영역 추가")}
    >
      <form onSubmit={submit} className="flex flex-col gap-4">
        <label className="flex flex-col gap-1">
          <span className={label}>{tr("이름")}</span>
          <input
            className={field}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={tr("예: 학업")}
            required
          />
        </label>
        <AreaIconPicker value={icon} name={name} onChange={setIcon} />
        <ErrorText error={write.error} />
        <div className="flex justify-end gap-2">
          <button type="button" className={btn.ghost} onClick={onClose}>
            {tr("취소")}
          </button>
          <button type="submit" className={btn.cta} disabled={write.isPending}>
            {area ? tr("저장") : tr("추가하기")}
          </button>
        </div>
      </form>
    </Dialog>
  );
}
