import { Command } from "cmdk";
import { useNavigate } from "react-router";
import { STATUSES, useChannels, useTasks } from "../api";

const item =
  "flex h-10 cursor-pointer items-center gap-2.5 rounded-xl px-3 text-[14px] text-text-2 data-[selected=true]:bg-inset data-[selected=true]:text-ink";
const group =
  "[&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:pt-3 [&_[cmdk-group-heading]]:pb-1.5 [&_[cmdk-group-heading]]:text-[11.5px] [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-meta";

/** ⌘K: jump to a channel or open a task in its channel's kanban. */
export function QuickSwitcher({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const channels = useChannels();
  const tasks = useTasks();
  const byId = new Map(channels.data?.channels.map((c) => [c.id, c]));
  const statusLabel = new Map(STATUSES.map((s) => [s.id, s.label]));

  const go = (path: string) => {
    onClose();
    navigate(path);
  };

  return (
    <Command.Dialog
      open={open}
      onOpenChange={(next) => !next && onClose()}
      label="채널·할 일 찾기"
      overlayClassName="fixed inset-0 bg-black/30"
      contentClassName="fixed top-[14vh] left-1/2 w-[560px] max-w-[calc(100vw-32px)] -translate-x-1/2 overflow-hidden rounded-3xl border border-line-soft bg-card text-text shadow-lift"
    >
      <Command.Input
        placeholder="채널이나 할 일 이름"
        className="h-14 w-full border-0 border-b border-line-soft bg-transparent px-5 text-[15px] text-text outline-none placeholder:text-meta focus-visible:outline-none"
      />
      <Command.List className="max-h-[50vh] overflow-y-auto p-2">
        <Command.Empty className="px-3 py-6 text-center text-[13px] text-meta">
          찾는 게 없어요
        </Command.Empty>
        <Command.Group heading="채널" className={group}>
          {channels.data?.channels.map((c) => (
            <Command.Item
              key={c.id}
              value={`channel ${c.name}`}
              onSelect={() =>
                go(
                  c.kind === "system" && c.name === "today"
                    ? "/"
                    : `/c/${c.id}`,
                )
              }
              className={item}
            >
              <span className="font-mono text-hash">#</span>
              {c.name}
            </Command.Item>
          ))}
        </Command.Group>
        <Command.Group heading="할 일" className={group}>
          {tasks.data?.map((t) => (
            <Command.Item
              key={t.id}
              value={`task ${t.title} ${t.id}`}
              onSelect={() => go(`/c/${t.channel_id}/kanban?task=${t.id}`)}
              className={item}
            >
              <span className="grow truncate">{t.title}</span>
              <span className="text-[12px] text-meta">
                #{byId.get(t.channel_id)?.name} · {statusLabel.get(t.status)}
              </span>
            </Command.Item>
          ))}
        </Command.Group>
      </Command.List>
    </Command.Dialog>
  );
}
