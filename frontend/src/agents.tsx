import { tr } from "./i18n"; /** Agent identity colours (Tokens: only agents get their own low-chroma hue). Unknown
 * or custom agents fall back to the neutral ink step. */

const KNOWN: Record<
  string,
  { name: string; initials: string; bg: string; text: string }
> = {
  hermes: {
    name: "Hermes",
    initials: "H",
    bg: "var(--hermes)",
    text: "var(--hermes-text)",
  },
  claude: {
    name: "Claude",
    initials: "C",
    bg: "var(--claude)",
    text: "var(--claude-text)",
  },
  codex: {
    name: "Codex",
    initials: "Cx",
    bg: "var(--codex)",
    text: "var(--codex-text)",
  },
  local: {
    name: tr("로컬 모델"),
    initials: "L",
    bg: "var(--step-4)",
    text: "var(--text-2)",
  },
};

type Info = { name: string; initials: string; bg: string; text: string };

// Custom agents (PLAN Phase 10), filled from the agent list by the shell.
const custom = new Map<string, Info>();

export function registerAgents(
  list: {
    name: string;
    display_name: string;
    avatar?: string | null;
    is_builtin: boolean;
  }[],
) {
  custom.clear();
  for (const a of list) {
    if (a.is_builtin) continue;
    custom.set(a.name, {
      name: a.display_name,
      initials: a.avatar || a.display_name.slice(0, 1),
      bg: "var(--step-4)",
      text: "var(--text-2)",
    });
  }
}

export function agentInfo(id: string | null | undefined): Info {
  const key = (id ?? "").toLowerCase().replace(/^agent:/, "");
  return (
    KNOWN[key] ??
    custom.get(key) ?? {
      name: key || tr("에이전트"),
      initials: (key || "?").slice(0, 2).toUpperCase(),
      bg: "var(--step-4)",
      text: "var(--text-2)",
    }
  );
}

export function AgentAvatar({
  id,
  size = 36,
}: {
  id: string | null | undefined;
  size?: number;
}) {
  const agent = agentInfo(id);
  return (
    <span
      className="flex shrink-0 items-center justify-center rounded-full font-semibold text-on-dark"
      style={{
        width: size,
        height: size,
        background: agent.bg,
        fontSize: size * (agent.initials.length > 1 ? 0.42 : 0.48),
      }}
      aria-hidden="true"
    >
      {agent.initials}
    </span>
  );
}
