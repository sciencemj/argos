import { type FormEvent, useState } from "react";
import { NavLink, useMatch, useNavigate } from "react-router";
import { useChannels, useCreateArea } from "../api";
import { DogIcon, PlusIcon, SettingsIcon } from "../icons";
import { btn, Dialog, ErrorText, field, label } from "../ui";

const railButton =
  "flex size-[42px] cursor-pointer items-center justify-center rounded-xl border border-transparent text-[15px]";
const active = "border-line bg-card font-semibold text-ink shadow-raised";

export function Rail({
  areaId,
  onPickArea,
}: {
  areaId: string | null;
  onPickArea: (id: string | null) => void;
}) {
  const navigate = useNavigate();
  const { data } = useChannels();
  const [adding, setAdding] = useState(false);
  const onSettings = useMatch("/settings") !== null;

  const openArea = (id: string) => {
    onPickArea(id);
    const first = data?.channels.find((c) => c.area_id === id);
    navigate(first ? `/c/${first.id}` : "/");
  };

  return (
    <nav
      aria-label="영역"
      className="flex flex-col items-center gap-2.5 border-r border-line-soft bg-rail py-3.5"
    >
      <div className="flex size-[42px] items-center justify-center rounded-xl bg-step-5 text-on-dark">
        <DogIcon />
      </div>
      <div className="my-1 h-px w-6 bg-line" />
      <button
        type="button"
        aria-label="홈 · 오늘"
        className={`${railButton} ${areaId === null && !onSettings ? active : "text-text-3"}`}
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
          title={area.name}
          className={`${railButton} ${area.id === areaId ? active : "text-text-3"}`}
          onClick={() => openArea(area.id)}
        >
          {area.icon || area.name.slice(0, 1)}
        </button>
      ))}
      <button
        type="button"
        aria-label="영역 추가"
        className={`${railButton} border-dashed !border-line text-meta`}
        onClick={() => setAdding(true)}
      >
        <PlusIcon size={17} />
      </button>
      <span className="grow" />
      <NavLink
        to="/settings"
        aria-label="설정"
        title="설정"
        className={({ isActive }) =>
          `${railButton} ${isActive ? active : "text-text-3"}`
        }
      >
        <SettingsIcon size={18} />
      </NavLink>
      <AddAreaDialog open={adding} onClose={() => setAdding(false)} />
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
