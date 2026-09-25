import type {
  DateSelectArg,
  DatesSetArg,
  EventChangeArg,
  EventClickArg,
  EventInput,
} from "@fullcalendar/core";
import koLocale from "@fullcalendar/core/locales/ko";
import dayGridPlugin from "@fullcalendar/daygrid";
import interactionPlugin from "@fullcalendar/interaction";
import FullCalendar from "@fullcalendar/react";
import timeGridPlugin from "@fullcalendar/timegrid";
import { type FormEvent, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";
import {
  type CalEvent,
  useCalendarConflicts,
  useCreateEvent,
  useDeleteEvent,
  useEvents,
  useTasks,
  useUpdateEvent,
} from "../api";
import { fmt } from "../dates";
import { btn, Chip, Dialog, ErrorText, field, label } from "../ui";
import { useChannel } from "./ChannelPage";

type Draft = {
  allDay: boolean;
  start: Date;
  end: Date;
  startStr: string;
  endStr: string;
};

const toInput = (e: CalEvent): EventInput =>
  e.all_day
    ? {
        id: e.id,
        title: e.title,
        start: e.start_date ?? undefined,
        end: e.end_date ?? undefined,
        allDay: true,
        editable: !e.rrule && !e.read_only,
      }
    : {
        id: e.id,
        title: e.title,
        start: e.starts_at ?? undefined,
        end: e.ends_at ?? undefined,
        editable: !e.rrule && !e.read_only,
      };

/** Week/month views (PLAN Phase 2). Times render in the browser's zone, which for this
 * local-first app is the configured display zone. */
export function CalendarTab() {
  const channel = useChannel();
  const [params, setParams] = useSearchParams();
  const [range, setRange] = useState<{ start: string; end: string } | null>(
    null,
  );
  const [draft, setDraft] = useState<Draft | null>(null);
  const [selected, setSelected] = useState<CalEvent | null>(null);
  const events = useEvents(range?.start ?? "", range?.end ?? "", channel.id);
  const tasks = useTasks(channel.id);
  const update = useUpdateEvent();
  const conflicts = useCalendarConflicts();

  const sources = useMemo<EventInput[]>(() => {
    const items = range ? (events.data ?? []).map(toInput) : [];
    const deadlines = (tasks.data ?? [])
      .filter((t) => t.due_at && t.status !== "done")
      .map<EventInput>((t) => ({
        id: `task:${t.id}`,
        title: `마감 · ${t.title}`,
        start: t.due_at ?? undefined,
        classNames: ["argos-deadline"],
        editable: false,
      }));
    return [...items, ...deadlines];
  }, [events.data, tasks.data, range]);

  const onDates = (arg: DatesSetArg) =>
    setRange({ start: arg.start.toISOString(), end: arg.end.toISOString() });

  const onSelect = (arg: DateSelectArg) =>
    setDraft({
      allDay: arg.allDay,
      start: arg.start,
      end: arg.end,
      startStr: arg.startStr,
      endStr: arg.endStr,
    });

  const onClick = ({ event }: EventClickArg) => {
    if (event.id.startsWith("task:")) {
      const next = new URLSearchParams(params);
      next.set("task", event.id.slice(5));
      setParams(next);
      return;
    }
    setSelected(events.data?.find((e) => e.id === event.id) ?? null);
  };

  const onChange = ({ event, revert }: EventChangeArg) => {
    const changes = event.allDay
      ? {
          start_date: event.startStr.slice(0, 10),
          end_date: event.endStr ? event.endStr.slice(0, 10) : null,
          starts_at: null,
          ends_at: null,
        }
      : {
          starts_at: event.start?.toISOString() ?? null,
          ends_at: event.end?.toISOString() ?? null,
          start_date: null,
          end_date: null,
        };
    update.mutate({ id: event.id, ...changes }, { onError: revert });
  };

  return (
    <div className="flex min-h-0 grow flex-col px-8 pb-[22px]">
      {(conflicts.data?.length ?? 0) > 0 && (
        <div
          role="alert"
          className="mb-3 flex items-center gap-3 rounded-2xl bg-danger-bg px-4 py-2.5 text-[13px] text-danger"
        >
          <span className="grow">
            앱과 캘린더에서 같은 일정을 각각 고쳤어요 ({conflicts.data?.length}
            건). 어느 쪽을 남길지 골라 주세요.
          </span>
          <Link
            to="/settings#calendar-conflicts"
            className="font-medium underline"
          >
            고르러 가기
          </Link>
        </div>
      )}
      <ErrorText error={update.error} />
      <div className="min-h-0 grow rounded-3xl border border-line-soft bg-card p-4 shadow-sm">
        <FullCalendar
          plugins={[timeGridPlugin, dayGridPlugin, interactionPlugin]}
          initialView="timeGridWeek"
          locale={koLocale}
          height="100%"
          headerToolbar={{
            left: "prev,next today",
            center: "title",
            right: "timeGridWeek,dayGridMonth",
          }}
          nowIndicator
          selectable
          selectMirror
          editable
          events={sources}
          datesSet={onDates}
          select={onSelect}
          eventClick={onClick}
          eventChange={onChange}
          slotMinTime="07:00:00"
          scrollTime="08:00:00"
        />
      </div>
      <NewEventDialog
        channelId={channel.id}
        draft={draft}
        onClose={() => setDraft(null)}
      />
      <EventDialog event={selected} onClose={() => setSelected(null)} />
    </div>
  );
}

function NewEventDialog({
  channelId,
  draft,
  onClose,
}: {
  channelId: string;
  draft: Draft | null;
  onClose: () => void;
}) {
  const create = useCreateEvent();
  const [title, setTitle] = useState("");

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!draft) return;
    const when = draft.allDay
      ? {
          start_date: draft.startStr.slice(0, 10),
          end_date: draft.endStr.slice(0, 10),
        }
      : {
          starts_at: draft.start.toISOString(),
          ends_at: draft.end.toISOString(),
        };
    create.mutate(
      { channel_id: channelId, title: title.trim(), ...when },
      {
        onSuccess: () => {
          setTitle("");
          onClose();
        },
      },
    );
  };

  const summary = draft
    ? draft.allDay
      ? `${fmt(draft.start, "M/d (EEE)")} 종일`
      : `${fmt(draft.start, "M/d (EEE) HH:mm")} – ${fmt(draft.end, "HH:mm")}`
    : "";

  return (
    <Dialog open={draft !== null} onClose={onClose} title="일정 추가">
      <form onSubmit={submit} className="flex flex-col gap-4">
        <div className="font-mono text-[12.5px] text-text-3">{summary}</div>
        <label className="flex flex-col gap-1">
          <span className={label}>제목</span>
          <input
            className={field}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="예: 3주차 퀴즈"
            required
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

function EventDialog({
  event,
  onClose,
}: {
  event: CalEvent | null;
  onClose: () => void;
}) {
  const update = useUpdateEvent();
  const remove = useDeleteEvent();
  const [title, setTitle] = useState("");
  const [seen, setSeen] = useState<string | null>(null);
  if (event && event.id !== seen) {
    setSeen(event.id);
    setTitle(event.title);
  }

  // Other Apple calendars and recurring series change only in the calendar app (7b).
  const readOnly = Boolean(event?.read_only);
  const when = event
    ? event.all_day
      ? `${fmt(event.start_date ?? "", "M/d (EEE)")} 종일`
      : `${fmt(event.starts_at ?? "", "M/d (EEE) HH:mm")}${event.ends_at ? ` – ${fmt(event.ends_at, "HH:mm")}` : ""}`
    : "";

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (event)
      update.mutate(
        { id: event.id, title: title.trim() },
        { onSuccess: onClose },
      );
  };

  return (
    <Dialog open={event !== null} onClose={onClose} title="일정">
      <form onSubmit={submit} className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-[12.5px] text-text-3">{when}</span>
          {event?.source && <Chip>{event.source}</Chip>}
          {event?.rrule && <Chip>반복</Chip>}
        </div>
        {readOnly && (
          <p className="m-0 text-[12.5px] text-text-3">
            {event?.rrule
              ? "반복 일정은 캘린더 앱에서 고쳐 주세요."
              : `'${event?.source}' 캘린더의 일정이라 캘린더 앱에서 고쳐 주세요.`}
          </p>
        )}
        <label className="flex flex-col gap-1">
          <span className={label}>제목</span>
          <input
            className={field}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            readOnly={readOnly}
            required
          />
        </label>
        <ErrorText error={update.error ?? remove.error} />
        <div className="flex items-center gap-2">
          <button
            type="button"
            className={btn.danger}
            hidden={readOnly}
            onClick={() =>
              event &&
              confirm(`"${event.title}" 일정을 삭제할까요?`) &&
              remove.mutate(event.id, { onSuccess: onClose })
            }
          >
            삭제
          </button>
          <span className="grow" />
          <button type="button" className={btn.ghost} onClick={onClose}>
            닫기
          </button>
          {!readOnly && (
            <button
              type="submit"
              className={btn.cta}
              disabled={update.isPending}
            >
              저장
            </button>
          )}
        </div>
      </form>
    </Dialog>
  );
}
