import { useState } from "react";
import { Link } from "react-router";
import {
  type SetupTool,
  useConnectTool,
  useDisconnectTool,
  useSetup,
} from "../api";
import { language, t, tr } from "../i18n";
import { btn, ConfirmDialog, card, ErrorText } from "../ui";

// Why each tool matters to Argos, in the words of the setup screen.
const USE: Record<string, string> = {
  claude: tr(
    "채팅·코딩 잡 에이전트. 연결하면 터미널의 Claude Code도 Argos 도구를 써요.",
  ),
  codex: tr(
    "채팅·코딩 잡 에이전트. 연결하면 터미널의 Codex도 Argos 도구를 써요.",
  ),
  hermes: tr(
    "기본 대화 에이전트(메신저 알림 포함). 연결해야 Argos에 일정·할 일을 기록해요.",
  ),
  antigravity: tr(
    "Google Antigravity CLI(agy). 연결하면 agy도 MCP로 Argos 도구를 쓰고 argos 스킬을 읽어요.",
    "Google's Antigravity CLI (agy). Once connected, agy uses the Argos tools over MCP and reads the argos skill.",
  ),
  antigravity_ide: tr(
    "Antigravity 앱. 연결하면 앱의 mcp_config.json에 argos 항목만 더하고, 다른 MCP 서버 설정은 그대로 둬요.",
    "The Antigravity app. Connecting adds only an argos entry to its mcp_config.json and leaves your other MCP servers as they are.",
  ),
  ollama: tr("인박스 분류와 비슷한 메모 묶기를 이 Mac에서 돌리는 로컬 모델."),
};

type Parts = { mcp: boolean; skill: boolean };

function partsLabel({ mcp, skill }: Parts) {
  return mcp && skill
    ? t("MCP와 스킬을", "MCP connection and skill")
    : mcp
      ? t("MCP를", "MCP connection")
      : t("스킬을", "skill");
}

/** One part of a connection (MCP entry or skill): its state and the button that flips it. */
function Part({
  name,
  on,
  onLabel,
  offLabel,
  add,
  remove,
  busy,
  pending,
  onAdd,
  onRemove,
}: {
  name: string;
  on: boolean;
  onLabel: string;
  offLabel: string;
  add: string;
  remove: string;
  busy: boolean;
  pending: boolean;
  onAdd: () => void;
  onRemove: () => void;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="w-10 shrink-0 text-[12px] font-medium text-text-2">
        {name}
      </span>
      <span
        className={`flex items-center gap-1.5 text-[12.5px] ${on ? "text-ink" : "text-meta"}`}
      >
        <span
          aria-hidden
          className={`size-1.5 rounded-full ${on ? "bg-ink" : "bg-line"}`}
        />
        {on ? onLabel : offLabel}
      </span>
      <span className="grow" />
      <button
        type="button"
        className={
          on
            ? "inline-flex h-7 cursor-pointer items-center rounded-full px-2.5 text-[12.5px] text-text-3 hover:text-ink disabled:opacity-50"
            : "inline-flex h-7 cursor-pointer items-center rounded-full border border-line bg-card px-3 text-[12.5px] font-medium text-text disabled:opacity-50"
        }
        disabled={busy}
        aria-label={`${name} ${on ? remove : add}`}
        onClick={on ? onRemove : onAdd}
      >
        {pending ? "…" : on ? remove : add}
      </button>
    </div>
  );
}

export function ToolRow({ tool }: { tool: SetupTool }) {
  const connect = useConnectTool();
  const disconnect = useDisconnectTool();
  const [removing, setRemoving] = useState<Parts | null>(null);
  const busy = connect.isPending || disconnect.isPending;
  // Which button is working: the parts the running request adds or removes.
  const adding = connect.isPending ? connect.variables : undefined;
  const dropping = disconnect.isPending ? disconnect.variables : undefined;
  const addingAll = !!adding && adding.mcp !== false && adding.skill !== false;
  const missing = !tool.connected || (tool.skill_supported && !tool.skill);
  const partial =
    tool.skill_supported && (tool.connected || tool.skill) && missing;

  return (
    <li className="flex flex-col gap-2 rounded-2xl border border-line-soft px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[14px] font-medium text-ink">{tool.label}</span>
        <span className="min-w-0 grow truncate font-mono text-[11.5px] text-meta">
          {tool.installed ? (tool.version ?? tool.path) : ""}
        </span>
        {!tool.installed ? (
          <span className="text-[12px] text-text-3">{tr("설치 안 됨")}</span>
        ) : !tool.connectable ? (
          <span className="text-[12px] text-text-3">{tr("설치됨")}</span>
        ) : missing ? (
          <button
            type="button"
            className={`${btn.outline} shrink-0`}
            disabled={busy}
            onClick={() => connect.mutate({ name: tool.name })}
          >
            {addingAll
              ? tr("연결하는 중…")
              : partial
                ? t("나머지 연결", "Connect the rest")
                : tr("Argos에 연결")}
          </button>
        ) : (
          <button
            type="button"
            className={`${btn.ghost} shrink-0`}
            disabled={busy}
            onClick={() =>
              setRemoving({ mcp: true, skill: tool.skill_supported })
            }
          >
            {tool.skill_supported ? tr("모두 제거") : tr("MCP 제거")}
          </button>
        )}
      </div>
      <span className="text-[12.5px] leading-relaxed text-text-3">
        {USE[tool.name]}
      </span>
      {!tool.installed && (
        <span className="text-[12px] text-meta">{tool.hint}</span>
      )}
      {tool.installed && tool.connectable && (
        <div className="flex flex-col gap-1 rounded-xl bg-inset px-3 py-1.5">
          <Part
            name="MCP"
            on={tool.connected}
            onLabel={t("연결됨", "Connected")}
            offLabel={t("연결 안 됨", "Not connected")}
            add={t("연결", "Connect")}
            remove={t("해제", "Disconnect")}
            busy={busy}
            pending={
              adding?.skill === false || (!!dropping?.mcp && !dropping.skill)
            }
            onAdd={() =>
              connect.mutate({ name: tool.name, mcp: true, skill: false })
            }
            onRemove={() => setRemoving({ mcp: true, skill: false })}
          />
          {tool.skill_supported ? (
            <Part
              name={tr("스킬")}
              on={tool.skill}
              onLabel={t("설치됨", "Installed")}
              offLabel={t("설치 안 됨", "Not installed")}
              add={t("설치", "Install")}
              remove={t("제거", "Remove")}
              busy={busy}
              pending={
                adding?.mcp === false || (!!dropping?.skill && !dropping.mcp)
              }
              onAdd={() =>
                connect.mutate({ name: tool.name, mcp: false, skill: true })
              }
              onRemove={() => setRemoving({ mcp: false, skill: true })}
            />
          ) : (
            <span className="text-[12px] text-meta">
              {t(
                "이 도구는 스킬을 읽지 않아 MCP만 연결해요.",
                "This tool doesn't read skills, so only MCP is connected.",
              )}
            </span>
          )}
        </div>
      )}
      {tool.skill && tool.skill_path && (
        <span className="truncate font-mono text-[11px] text-meta">
          {tool.skill_path}
        </span>
      )}
      {tool.shared_with && (
        <span className="text-[12px] text-meta">
          {t(
            "Antigravity CLI와 같은 설정 파일을 써요. 한쪽에서 연결하면 둘 다 연결돼요.",
            "Shares its config file with the Antigravity CLI, so connecting one connects both.",
          )}
        </span>
      )}
      {tool.skill_conflict && (
        <span className="text-[12px] text-danger">
          {tool.skill_path}
          {tr("에 Argos가 설치하지 않은 argos 스킬이 있어 건드리지 않아요.")}
        </span>
      )}
      <ErrorText error={connect.error ?? disconnect.error} />
      <ConfirmDialog
        open={removing !== null}
        title={t("Argos 연결 해제", "Remove from Argos")}
        message={
          removing
            ? t(
                `${tool.label}에서 Argos ${partsLabel(removing)} 지울까요?`,
                `Remove the Argos ${partsLabel(removing)} from ${tool.label}?`,
              )
            : ""
        }
        confirmLabel={t("제거", "Remove")}
        danger
        onConfirm={() => {
          if (removing) disconnect.mutate({ name: tool.name, ...removing });
        }}
        onClose={() => setRemoving(null)}
      />
    </li>
  );
}

/** Agent tools on this Mac and their connection to Argos: the MCP entry and the argos
 * skill, each on its own. The same list on the setup screen and in 설정. */
export function ToolsSection({ embedded = false }: { embedded?: boolean }) {
  const setup = useSetup();
  const tools = setup.data?.tools ?? [];
  return (
    <section
      id="tools"
      aria-label={t("도구 연결", "Tool integrations")}
      className={
        embedded
          ? "flex flex-col gap-3"
          : `${card} flex w-full flex-col gap-4 p-6`
      }
    >
      {!embedded && (
        <h2 className="m-0 text-[20px] font-light tracking-[-0.02em] text-ink">
          {t("도구 연결", "Tool integrations")}
        </h2>
      )}
      <p className="m-0 text-[13px] leading-relaxed text-text-3">
        {language() === "ko" ? (
          <>
            이 Mac에 설치된 도구를 찾았어요. <b>MCP</b>는 각 도구에 Argos 주소를
            등록하고(<code>mcp add</code>, Hermes는 <code>config set</code>,
            Antigravity 앱은 <code>mcp_config.json</code>), <b>스킬</b>은 Argos
            도구 쓰는 법을 담은 <code>argos</code> 스킬을 설치해요. 따로 켜고 끌
            수 있고, 누르기 전에는 도구 설정을 바꾸지 않아요.
          </>
        ) : (
          <>
            We found the agent tools installed on this Mac. <b>MCP</b> registers
            the Argos address with each tool (<code>mcp add</code>,{" "}
            <code>config set</code> for Hermes, <code>mcp_config.json</code> for
            the Antigravity app) and <b>Skill</b> installs the{" "}
            <code>argos</code> skill. Each can be turned on and off on its own,
            and nothing changes until you press a button.
          </>
        )}
      </p>
      {setup.isLoading && (
        <span className="text-[12.5px] text-meta">{tr("도구를 찾는 중…")}</span>
      )}
      <ul className="m-0 flex list-none flex-col gap-2 p-0">
        {tools.map((tool) => (
          <ToolRow key={tool.name} tool={tool} />
        ))}
      </ul>
      <div className="flex items-center gap-3">
        <button
          type="button"
          className={btn.ghost}
          disabled={setup.isFetching}
          onClick={() => void setup.refetch()}
        >
          {setup.isFetching ? tr("다시 찾는 중…") : tr("다시 찾기")}
        </button>
        {!embedded && (
          <Link
            to="/welcome"
            className="text-[12.5px] text-text-3 hover:text-ink"
          >
            {tr("처음 설정 다시 보기")}
          </Link>
        )}
      </div>
      <ErrorText error={setup.error} />
    </section>
  );
}
