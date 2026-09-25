import { type FormEvent, useState } from "react";
import {
  NavLink,
  Outlet,
  useNavigate,
  useOutletContext,
  useParams,
} from "react-router";
import { AgentAvatar } from "../agents";
import {
  ApiError,
  type Channel,
  useAgents,
  useChannels,
  useDeleteChannel,
  useUpdateChannel,
} from "../api";
import { Feed } from "../feed";
import { SettingsIcon } from "../icons";
import { btn, Dialog, ErrorText, field, label } from "../ui";

const tab =
  "flex h-8 items-center rounded-full border border-transparent px-3.5 text-text-3 aria-[current=page]:border-line-soft aria-[current=page]:bg-card aria-[current=page]:font-medium aria-[current=page]:text-ink aria-[current=page]:shadow-sm";

export const useChannel = () => useOutletContext<Channel>();

export function ChannelPage() {
  const { channelId } = useParams();
  const { data, isLoading } = useChannels();
  const [editing, setEditing] = useState(false);
  const channel = data?.channels.find((c) => c.id === channelId);

  if (isLoading) return null;
  if (!channel) {
    return (
      <p className="p-8 text-text-3">
        채널을 찾을 수 없어요. 삭제되었을 수 있어요.
      </p>
    );
  }
  const editable = channel.kind !== "system" && channel.kind !== "dm";

  if (channel.kind === "dm") {
    return <DMPage channel={channel} />;
  }

  return (
    <>
      <header className="flex flex-col gap-3 px-8 pt-[18px] pb-4">
        <div className="flex items-center gap-3.5">
          <h1 className="m-0 grow truncate text-[28px] leading-tight font-light tracking-[-0.02em] text-ink">
            <span className="text-hash">#</span> {channel.name}
          </h1>
          {editable && (
            <button
              type="button"
              aria-label="채널 설정"
              className="flex size-9 cursor-pointer items-center justify-center rounded-full border border-line-soft bg-card text-text-3 hover:text-ink"
              onClick={() => setEditing(true)}
            >
              <SettingsIcon />
            </button>
          )}
        </div>
        <div className="flex items-center gap-3.5">
          <nav
            aria-label="채널 탭"
            className="flex gap-0.5 rounded-full bg-inset p-[3px] text-[13.5px] whitespace-nowrap"
          >
            <NavLink to="." end className={tab}>
              피드
            </NavLink>
            <NavLink to="kanban" className={tab}>
              칸반
            </NavLink>
            <NavLink to="calendar" className={tab}>
              캘린더
            </NavLink>
          </nav>
          <span className="grow" />
          {channel.vault_path && (
            <span className="truncate text-[12.5px] text-meta">
              볼트 <span className="font-mono">{channel.vault_path}</span>
            </span>
          )}
        </div>
      </header>
      <Outlet context={channel} />
      {editable && (
        <ChannelSettings
          key={channel.id}
          channel={channel}
          open={editing}
          onClose={() => setEditing(false)}
        />
      )}
    </>
  );
}

/** 1:1 conversation with an agent: no kanban/calendar, the whole page is the chat. */
function DMPage({ channel }: { channel: Channel }) {
  const agents = useAgents();
  const agent = agents.data?.find((a) => a.id === channel.default_agent_id);
  return (
    <>
      <header className="flex items-center gap-3.5 px-8 pt-[18px] pb-4">
        <AgentAvatar id={agent?.name} size={36} />
        <div className="flex grow flex-col">
          <h1 className="m-0 text-[28px] leading-tight font-light tracking-[-0.02em] text-ink">
            {agent?.display_name ?? "에이전트"}
          </h1>
          {agent && !agent.available && (
            <span className="text-[12.5px] text-danger">{agent.problem}</span>
          )}
        </div>
      </header>
      <Feed key={channel.id} channel={channel} />
    </>
  );
}

export function FeedTab() {
  const channel = useChannel();
  return <Feed key={channel.id} channel={channel} />;
}

function ChannelSettings({
  channel,
  open,
  onClose,
}: {
  channel: Channel;
  open: boolean;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const update = useUpdateChannel();
  const remove = useDeleteChannel();
  const [name, setName] = useState(channel.name);
  const [vaultPath, setVaultPath] = useState(channel.vault_path ?? "");
  const [agentId, setAgentId] = useState(channel.default_agent_id ?? "");
  const agents = useAgents();

  const submit = (e: FormEvent) => {
    e.preventDefault();
    update.mutate(
      {
        id: channel.id,
        name: name.trim(),
        vault_path: vaultPath.trim() || null,
        default_agent_id: agentId || null,
      },
      { onSuccess: onClose },
    );
  };

  const destroy = (force: boolean) =>
    remove.mutate(
      { id: channel.id, force },
      {
        onSuccess: () => {
          onClose();
          navigate("/");
        },
        onError: (err) => {
          // A channel that still holds tasks/events needs an explicit second confirmation.
          if (err instanceof ApiError && err.code === "conflict" && !force) {
            if (confirm(`${err.message}\n\n할 일과 일정까지 모두 지울까요?`))
              destroy(true);
          }
        },
      },
    );

  return (
    <Dialog open={open} onClose={onClose} title="채널 설정">
      <form onSubmit={submit} className="flex flex-col gap-4">
        <label className="flex flex-col gap-1">
          <span className={label}>이름</span>
          <input
            className={field}
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className={label}>기본 에이전트 (/ask를 받음)</span>
          <select
            className={field}
            value={agentId}
            onChange={(e) => setAgentId(e.target.value)}
          >
            <option value="">앱 기본값 따르기</option>
            {agents.data?.map((a) => (
              <option key={a.id} value={a.id}>
                {a.display_name}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className={label}>옵시디언 폴더 (선택)</span>
          <input
            className={`${field} font-mono text-[13px]`}
            value={vaultPath}
            onChange={(e) => setVaultPath(e.target.value)}
            placeholder="강의/과목명/"
          />
        </label>
        <ErrorText error={update.error} />
        <div className="flex items-center gap-2">
          {channel.kind !== "personal" && (
            <button
              type="button"
              className={btn.danger}
              disabled={remove.isPending}
              onClick={() =>
                confirm(`#${channel.name} 채널을 삭제할까요?`) && destroy(false)
              }
            >
              채널 삭제
            </button>
          )}
          <span className="grow" />
          <button type="button" className={btn.ghost} onClick={onClose}>
            취소
          </button>
          <button type="submit" className={btn.cta} disabled={update.isPending}>
            저장
          </button>
        </div>
      </form>
    </Dialog>
  );
}
