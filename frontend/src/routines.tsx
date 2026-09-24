import { type FormEvent, useState } from "react";
import {
  type Routine,
  useCheckRoutine,
  useCreateRoutine,
  useDeleteRoutine,
  useRoutines,
  useUpdateRoutine,
} from "./api";
import { CheckIcon, PlusIcon, SettingsIcon } from "./icons";
import { btn, card, Dialog, ErrorText, field, label } from "./ui";

const DAYS = ["월", "화", "수", "목", "금", "토", "일"];

export function weekdaysLabel(weekdays: string): string {
  if (weekdays === "0123456") return "매일";
  if (weekdays === "01234") return "평일";
  if (weekdays === "56") return "주말";
  return [...weekdays].map((d) => DAYS[Number(d)]).join("·");
}

/** Daily checklist on Home: tick today's routines, see the running streak. */
export function RoutinePanel() {
  const { data } = useRoutines();
  const check = useCheckRoutine();
  const create = useCreateRoutine();
  const [title, setTitle] = useState("");
  const [editing, setEditing] = useState<Routine | null>(null);

  const today = data?.routines.filter((r) => r.scheduled) ?? [];
  const resting = data?.routines.filter((r) => !r.scheduled) ?? [];
  const doneCount = today.filter((r) => r.done).length;

  const add = (e: FormEvent) => {
    e.preventDefault();
    if (!title.trim()) return;
    create.mutate(
      { title: title.trim(), weekdays: "0123456" },
      { onSuccess: () => setTitle("") },
    );
  };

  return (
    <section
      aria-label="오늘의 루틴"
      className={`${card} flex flex-col gap-3 px-[22px] py-[18px]`}
    >
      <div className="flex items-baseline gap-2">
        <h2 className="m-0 grow text-[20px] font-light tracking-[-0.02em] text-ink">
          오늘의 루틴
        </h2>
        {today.length > 0 && (
          <span className="font-mono text-[12px] text-meta">
            {doneCount}/{today.length}
          </span>
        )}
      </div>

      {today.length > 0 && (
        <span className="flex h-[3px] overflow-hidden rounded-sm bg-line-soft">
          <span
            className="bg-step-5 transition-[width]"
            style={{ width: `${(doneCount / today.length) * 100}%` }}
          />
        </span>
      )}

      <ul className="m-0 flex list-none flex-col gap-1 p-0">
        {today.map((r) => (
          <li
            key={r.id}
            className="group flex items-center gap-2.5 rounded-xl px-1 py-1"
          >
            <label className="flex grow cursor-pointer items-center gap-2.5">
              <input
                type="checkbox"
                className="peer sr-only"
                checked={r.done}
                onChange={(e) =>
                  data &&
                  check.mutate({
                    id: r.id,
                    day: data.today,
                    done: e.target.checked,
                  })
                }
              />
              <span
                aria-hidden="true"
                className="flex size-[22px] shrink-0 items-center justify-center rounded-full border border-line text-transparent peer-checked:border-step-5 peer-checked:bg-step-5 peer-checked:text-on-dark peer-focus-visible:outline-2 peer-focus-visible:outline-cta"
              >
                <CheckIcon size={12} />
              </span>
              <span
                className={`grow ${r.done ? "text-meta line-through" : "text-text"}`}
              >
                {r.title}
              </span>
            </label>
            {r.streak > 0 && (
              <span className="font-mono text-[11.5px] whitespace-nowrap text-text-3">
                {r.streak}일 연속
              </span>
            )}
            <button
              type="button"
              aria-label={`${r.title} 편집`}
              className={`${btn.icon} opacity-0 group-focus-within:opacity-100 group-hover:opacity-100`}
              onClick={() => setEditing(r)}
            >
              <SettingsIcon size={14} />
            </button>
          </li>
        ))}
      </ul>

      {data && data.routines.length === 0 && (
        <p className="m-0 text-[13px] text-meta">
          매일 챙길 일을 적어 두면 여기서 체크하고 연속 기록이 쌓여요.
        </p>
      )}
      {resting.length > 0 && (
        <p className="m-0 text-[12px] text-meta">
          오늘 쉬는 루틴: {resting.map((r) => r.title).join(", ")}
        </p>
      )}

      <form onSubmit={add} className="flex items-center gap-2">
        <span className="text-meta">
          <PlusIcon size={14} />
        </span>
        <label htmlFor="new-routine" className="sr-only">
          루틴 추가
        </label>
        <input
          id="new-routine"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="루틴 추가 (매일)"
          className={`${field} h-9 text-[13.5px]`}
        />
      </form>
      <ErrorText error={create.error ?? check.error} />

      <RoutineDialog routine={editing} onClose={() => setEditing(null)} />
    </section>
  );
}

function RoutineDialog({
  routine,
  onClose,
}: {
  routine: Routine | null;
  onClose: () => void;
}) {
  const update = useUpdateRoutine();
  const remove = useDeleteRoutine();
  const [title, setTitle] = useState("");
  const [days, setDays] = useState("");
  const [seen, setSeen] = useState<string | null>(null);
  if (routine && routine.id !== seen) {
    setSeen(routine.id);
    setTitle(routine.title);
    setDays(routine.weekdays);
  }

  const toggle = (d: string) =>
    setDays((cur) =>
      cur.includes(d) ? cur.replace(d, "") : [...cur, d].sort().join(""),
    );

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (routine) {
      update.mutate(
        { id: routine.id, title: title.trim(), weekdays: days },
        { onSuccess: onClose },
      );
    }
  };

  return (
    <Dialog open={routine !== null} onClose={onClose} title="루틴 편집">
      <form onSubmit={submit} className="flex flex-col gap-4">
        <label className="flex flex-col gap-1">
          <span className={label}>이름</span>
          <input
            className={field}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
          />
        </label>
        <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
          <legend className={`${label} mb-2`}>
            반복 요일 · {weekdaysLabel(days || "0")}
          </legend>
          <div className="flex gap-1.5">
            {DAYS.map((name, i) => {
              const d = String(i);
              const on = days.includes(d);
              return (
                <button
                  key={d}
                  type="button"
                  aria-pressed={on}
                  onClick={() => toggle(d)}
                  className={`size-9 cursor-pointer rounded-full border text-[13px] ${on ? "border-step-5 bg-step-5 text-on-dark" : "border-line bg-card text-text-3"}`}
                >
                  {name}
                </button>
              );
            })}
          </div>
        </fieldset>
        <ErrorText error={update.error ?? remove.error} />
        <div className="flex items-center gap-2">
          <button
            type="button"
            className={btn.danger}
            onClick={() =>
              routine &&
              confirm(`"${routine.title}" 루틴과 체크 기록을 삭제할까요?`) &&
              remove.mutate(routine.id, { onSuccess: onClose })
            }
          >
            삭제
          </button>
          <span className="grow" />
          <button type="button" className={btn.ghost} onClick={onClose}>
            취소
          </button>
          <button
            type="submit"
            className={btn.cta}
            disabled={update.isPending || !days}
          >
            저장
          </button>
        </div>
      </form>
    </Dialog>
  );
}
