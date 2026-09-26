import {
  type QueryClient,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import createClient from "openapi-fetch";
import type { components, paths } from "./api-types";

type Schemas = components["schemas"];
export type Area = Schemas["AreaOut"];
export type Channel = Schemas["ChannelOut"];
export type Task = Schemas["TaskOut"];
export type TaskStatus = Task["status"];
export type CalEvent = Schemas["EventOut"];
export type InboxItem = Schemas["InboxOut"];
export type Activity = Schemas["ActivityOut"];
export type Config = Schemas["ConfigOut"];
export type Message = Schemas["MessageOut"];
export type Routine = Schemas["RoutineOut"];
export type Approval = Schemas["ApprovalOut"];
export type Agent = Schemas["AgentOut"];
export type Run = Schemas["RunOut"];
export type Routines = Schemas["RoutinesOut"];
export type InboxAccept = Schemas["InboxAccept"];
export type PromoteFields = Schemas["MessageConvert"];

export const STATUSES: { id: TaskStatus; label: string }[] = [
  { id: "backlog", label: "백로그" },
  { id: "todo", label: "할 일" },
  { id: "in_progress", label: "진행 중" },
  { id: "review", label: "검토" },
  { id: "done", label: "완료" },
];

export const client = createClient<paths>({
  baseUrl: globalThis.location?.origin,
});

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

/** Unwraps an openapi-fetch result, turning the uniform error body (PLAN §7.1) into ApiError. */
async function call<T>(
  request: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  const { data, error, response } = await request;
  if (!response.ok) {
    const body = (error ?? {}) as {
      error?: { code?: string; message?: string };
    };
    throw new ApiError(
      response.status,
      body.error?.code ?? "http_error",
      body.error?.message ?? `HTTP ${response.status}`,
    );
  }
  return data as T;
}

// --- invalidation ---------------------------------------------------------------

/** Query keys each object type feeds. Shared by mutations and the WebSocket hub. */
export function invalidateFor(
  qc: QueryClient,
  objectType: string,
  id?: string,
) {
  // Messages embed their task/event/inbox card, so those changes refresh feeds too.
  const feeds = [["messages"], ["thread"]];
  const keys: Record<string, string[][]> = {
    task: [["tasks"], ["today"], ["task"], ["activity"], ...feeds],
    event: [
      ["events"],
      ["today"],
      ["conflicts"],
      ["settings", "icloud"],
      ...feeds,
    ],
    inbox_item: [["inbox"], ["today"], ...feeds],
    message: feeds,
    routine: [["routines"]],
    approval: [["approvals"], ...feeds],
    agent_run: [["tasks"], ["task"], ...feeds], // job status lives on the card
    routine_check: [["routines"]],
    channel: [["channels"], ["tasks"], ["notes"], ["materials"]],
    note_ref: [["notes"], ["settings", "vault"]],
    agent: [["agents"], ["channels"]],
    debate: [["debates"], ...feeds],
    notification: [["notifications"]],
    area: [["channels"]],
  };
  for (const key of keys[objectType] ?? []) {
    void qc.invalidateQueries({ queryKey: key });
  }
  if (id && objectType === "task") {
    void qc.invalidateQueries({ queryKey: ["task", id] });
  }
}

// --- queries --------------------------------------------------------------------

export const useConfig = () =>
  useQuery({
    queryKey: ["config"],
    queryFn: () => call(client.GET("/api/v1/config")),
    staleTime: Number.POSITIVE_INFINITY,
  });

export const useChannels = () =>
  useQuery({
    queryKey: ["channels"],
    queryFn: () => call(client.GET("/api/v1/channels")),
  });

export const useTasks = (channelId?: string) =>
  useQuery({
    queryKey: ["tasks", channelId ?? "all"],
    queryFn: () =>
      call(
        client.GET("/api/v1/tasks", {
          params: { query: channelId ? { channel_id: channelId } : {} },
        }),
      ),
  });

export const useTask = (taskId: string | null) =>
  useQuery({
    queryKey: ["task", taskId],
    enabled: taskId !== null,
    queryFn: () =>
      call(
        client.GET("/api/v1/tasks/{task_id}", {
          params: { path: { task_id: taskId ?? "" } },
        }),
      ),
  });

export const useActivity = (taskId: string | null) =>
  useQuery({
    queryKey: ["activity", taskId],
    enabled: taskId !== null,
    queryFn: () =>
      call(
        client.GET("/api/v1/tasks/{task_id}/activity", {
          params: { path: { task_id: taskId ?? "" } },
        }),
      ),
  });

export const useToday = () =>
  useQuery({
    queryKey: ["today"],
    queryFn: () => call(client.GET("/api/v1/today")),
  });

export const useEvents = (start: string, end: string, channelId?: string) =>
  useQuery({
    queryKey: ["events", start, end, channelId ?? "all"],
    enabled: Boolean(start && end),
    queryFn: () =>
      call(
        client.GET("/api/v1/events", {
          params: {
            query: {
              start,
              end,
              ...(channelId ? { channel_id: channelId } : {}),
            },
          },
        }),
      ),
  });

export const useOpenInbox = () =>
  useQuery({
    queryKey: ["inbox", "open"],
    queryFn: () =>
      call(
        client.GET("/api/v1/inbox", {
          params: { query: { status: ["new", "suggested"] } },
        }),
      ),
  });

/** Newest page first from the API; pages are rendered oldest → newest. */
export const useMessages = (channelId: string, includePersonal = false) =>
  useInfiniteQuery({
    queryKey: ["messages", channelId, includePersonal],
    enabled: Boolean(channelId),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      call(
        client.GET("/api/v1/channels/{channel_id}/messages", {
          params: {
            path: { channel_id: channelId },
            query: {
              ...(pageParam ? { cursor: pageParam } : {}),
              ...(includePersonal ? { include_personal: true } : {}),
            },
          },
        }),
      ),
    getNextPageParam: (page) => page.next_cursor,
  });

export const useThread = (messageId: string | null) =>
  useQuery({
    queryKey: ["thread", messageId],
    enabled: messageId !== null,
    queryFn: () =>
      call(
        client.GET("/api/v1/messages/{message_id}/thread", {
          params: { path: { message_id: messageId ?? "" } },
        }),
      ),
  });

// --- mutations ------------------------------------------------------------------

function useWrite<A, R>(
  objectTypes: string | string[],
  fn: (args: A) => Promise<R>,
) {
  const qc = useQueryClient();
  const types = Array.isArray(objectTypes) ? objectTypes : [objectTypes];
  return useMutation({
    mutationFn: fn,
    onSettled: () => {
      for (const t of types) invalidateFor(qc, t);
    },
  });
}

// Writes that can create a task or event on top of their own object.
const PROMOTES = ["message", "inbox_item", "task", "event"];

export const useCreateTask = () =>
  useWrite("task", (body: Schemas["TaskCreate"]) =>
    call(client.POST("/api/v1/tasks", { body })),
  );

export const useUpdateTask = () =>
  useWrite("task", ({ id, ...body }: Schemas["TaskUpdate"] & { id: string }) =>
    call(
      client.PATCH("/api/v1/tasks/{task_id}", {
        params: { path: { task_id: id } },
        body,
      }),
    ),
  );

export const useDeleteTask = () =>
  useWrite("task", (id: string) =>
    call(
      client.DELETE("/api/v1/tasks/{task_id}", {
        params: { path: { task_id: id } },
      }),
    ),
  );

/** `optimistic` is the channel's task list as it should look after the move. */
export type MoveVars = Schemas["TaskMove"] & { id: string; optimistic: Task[] };

/** Kanban move with an optimistic cache write that happens synchronously, in the same
 * render as the drop: an async onMutate would let the old order flash for a frame. */
export function useMoveTask(channelId?: string) {
  const qc = useQueryClient();
  const key = ["tasks", channelId ?? "all"];
  const mutation = useMutation({
    mutationFn: ({ id, optimistic: _, ...body }: MoveVars) =>
      call(
        client.POST("/api/v1/tasks/{task_id}/move", {
          params: { path: { task_id: id } },
          body,
        }),
      ),
    onSettled: () => invalidateFor(qc, "task"),
  });

  const move = (vars: MoveVars) => {
    const previous = qc.getQueryData<Task[]>(key);
    // Stop an in-flight refetch without reverting, so it cannot overwrite the new order.
    void qc.cancelQueries({ queryKey: key }, { revert: false, silent: true });
    qc.setQueryData(key, vars.optimistic);
    mutation.mutate(vars, {
      onError: () => {
        if (previous) qc.setQueryData(key, previous);
      },
    });
  };

  return { move, error: mutation.error };
}

export const useCreateEvent = () =>
  useWrite("event", (body: Schemas["EventCreate"]) =>
    call(client.POST("/api/v1/events", { body })),
  );

export const useUpdateEvent = () =>
  useWrite(
    "event",
    ({ id, ...body }: Schemas["EventUpdate"] & { id: string }) =>
      call(
        client.PATCH("/api/v1/events/{event_id}", {
          params: { path: { event_id: id } },
          body,
        }),
      ),
  );

export const useDeleteEvent = () =>
  useWrite("event", (id: string) =>
    call(
      client.DELETE("/api/v1/events/{event_id}", {
        params: { path: { event_id: id } },
      }),
    ),
  );

export const useUpdateInbox = () =>
  useWrite(
    "inbox_item",
    ({ id, ...body }: Schemas["InboxUpdate"] & { id: string }) =>
      call(
        client.PATCH("/api/v1/inbox/{item_id}", {
          params: { path: { item_id: id } },
          body,
        }),
      ),
  );

export const useCreateArea = () =>
  useWrite("area", (body: Schemas["AreaCreate"]) =>
    call(client.POST("/api/v1/areas", { body })),
  );

export const useCreateChannel = () =>
  useWrite("channel", (body: Schemas["ChannelCreate"]) =>
    call(client.POST("/api/v1/channels", { body })),
  );

export const useUpdateChannel = () =>
  useWrite(
    "channel",
    ({ id, ...body }: Schemas["ChannelUpdate"] & { id: string }) =>
      call(
        client.PATCH("/api/v1/channels/{channel_id}", {
          params: { path: { channel_id: id } },
          body,
        }),
      ),
  );

export const useDeleteChannel = () =>
  useWrite("channel", ({ id, force }: { id: string; force: boolean }) =>
    call(
      client.DELETE("/api/v1/channels/{channel_id}", {
        params: { path: { channel_id: id }, query: { force } },
      }),
    ),
  );

export const usePostMessage = () =>
  useWrite(
    PROMOTES, // slash commands create tasks/events directly
    ({
      channelId,
      ...body
    }: Schemas["MessageCreate"] & { channelId: string }) =>
      call(
        client.POST("/api/v1/channels/{channel_id}/messages", {
          params: { path: { channel_id: channelId } },
          body,
        }),
      ),
  );

export const usePinMessage = () =>
  useWrite("message", ({ id, pinned }: { id: string; pinned: boolean }) =>
    call(
      client.PATCH("/api/v1/messages/{message_id}", {
        params: { path: { message_id: id } },
        body: { pinned },
      }),
    ),
  );

export const useConvertMessage = () =>
  useWrite(PROMOTES, ({ id, ...body }: PromoteFields & { id: string }) =>
    call(
      client.POST("/api/v1/messages/{message_id}/convert", {
        params: { path: { message_id: id } },
        body,
      }),
    ),
  );

export const useAcceptInbox = () =>
  useWrite(PROMOTES, ({ id, ...body }: InboxAccept & { id: string }) =>
    call(
      client.POST("/api/v1/inbox/{item_id}/accept", {
        params: { path: { item_id: id } },
        body,
      }),
    ),
  );

export const useReclassify = () =>
  useWrite("inbox_item", (id: string) =>
    call(
      client.POST("/api/v1/inbox/{item_id}/classify", {
        params: { path: { item_id: id } },
      }),
    ),
  );

// --- settings -------------------------------------------------------------------

export const useClassifierSettings = () =>
  useQuery({
    queryKey: ["settings", "classifier"],
    queryFn: () => call(client.GET("/api/v1/settings/classifier")),
  });

export const useOllamaModels = () =>
  useQuery({
    queryKey: ["settings", "ollama-models"],
    queryFn: () => call(client.GET("/api/v1/settings/classifier/models")),
    staleTime: 0,
  });

export function useSaveClassifier() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (model: string | null) =>
      call(client.PUT("/api/v1/settings/classifier", { body: { model } })),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: ["settings"] });
      void qc.invalidateQueries({ queryKey: ["config"] });
    },
  });
}

// --- routines -------------------------------------------------------------------

export const useRoutines = () =>
  useQuery({
    queryKey: ["routines"],
    queryFn: () => call(client.GET("/api/v1/routines")),
  });

export const useCreateRoutine = () =>
  useWrite("routine", (body: Schemas["RoutineCreate"]) =>
    call(client.POST("/api/v1/routines", { body })),
  );

export const useUpdateRoutine = () =>
  useWrite(
    "routine",
    ({ id, ...body }: Schemas["RoutineUpdate"] & { id: string }) =>
      call(
        client.PATCH("/api/v1/routines/{routine_id}", {
          params: { path: { routine_id: id } },
          body,
        }),
      ),
  );

export const useDeleteRoutine = () =>
  useWrite("routine", (id: string) =>
    call(
      client.DELETE("/api/v1/routines/{routine_id}", {
        params: { path: { routine_id: id } },
      }),
    ),
  );

/** Ticks instantly; the refetch afterwards brings the real streak. */
export function useCheckRoutine() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      day,
      done,
    }: {
      id: string;
      day: string;
      done: boolean;
    }) =>
      call(
        client.PUT("/api/v1/routines/{routine_id}/checks/{day}", {
          params: { path: { routine_id: id, day } },
          body: { done },
        }),
      ),
    onMutate: ({ id, done }) => {
      const previous = qc.getQueryData<Routines>(["routines"]);
      if (previous) {
        qc.setQueryData<Routines>(["routines"], {
          ...previous,
          routines: previous.routines.map((r) =>
            r.id === id
              ? { ...r, done, streak: Math.max(0, r.streak + (done ? 1 : -1)) }
              : r,
          ),
        });
      }
      return { previous };
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.previous) qc.setQueryData(["routines"], ctx.previous);
    },
    onSettled: () => invalidateFor(qc, "routine"),
  });
}

// --- approvals (PLAN P5) ---------------------------------------------------------

export const usePendingApprovals = () =>
  useQuery({
    queryKey: ["approvals", "pending"],
    queryFn: () =>
      call(
        client.GET("/api/v1/approvals", {
          params: { query: { status: "pending" } },
        }),
      ),
  });

export const useResolveApproval = () =>
  useWrite(
    ["approval", "task", "event"],
    ({ id, approve }: { id: string; approve: boolean }) =>
      approve
        ? call(
            client.POST("/api/v1/approvals/{approval_id}/approve", {
              params: { path: { approval_id: id } },
            }),
          )
        : call(
            client.POST("/api/v1/approvals/{approval_id}/reject", {
              params: { path: { approval_id: id } },
            }),
          ),
  );

// --- agents (PLAN Phase 5) --------------------------------------------------------

export const useAgents = () =>
  useQuery({
    queryKey: ["agents"],
    queryFn: () => call(client.GET("/api/v1/agents")),
  });

export const useAgentSettings = () =>
  useQuery({
    queryKey: ["settings", "agents"],
    queryFn: () => call(client.GET("/api/v1/settings/agents")),
  });

export function useSaveDefaultAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) =>
      call(
        client.PUT("/api/v1/settings/agents", {
          body: { default_agent: name },
        }),
      ),
    onSettled: () => void qc.invalidateQueries({ queryKey: ["settings"] }),
  });
}

export const useOpenDM = () =>
  useWrite("channel", (name: string) =>
    call(
      client.POST("/api/v1/agents/{name}/dm", { params: { path: { name } } }),
    ),
  );

export const cancelRun = (runId: string) =>
  call(
    client.POST("/api/v1/runs/{run_id}/cancel", {
      params: { path: { run_id: runId } },
    }),
  );

// --- coding jobs (PLAN Phase 6) ---------------------------------------------------

export type Job = Schemas["JobOut"];

export const useStartJob = () =>
  useWrite("task", ({ id, ...body }: Schemas["JobCreate"] & { id: string }) =>
    call(
      client.POST("/api/v1/tasks/{task_id}/jobs", {
        params: { path: { task_id: id } },
        body,
      }),
    ),
  );

export const useJobSettings = () =>
  useQuery({
    queryKey: ["settings", "jobs"],
    queryFn: () => call(client.GET("/api/v1/settings/jobs")),
  });

export function useSaveJobRoots() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (roots: string[]) =>
      call(client.PUT("/api/v1/settings/jobs", { body: { roots } })),
    onSuccess: (data) => {
      qc.setQueryData(["settings", "jobs"], data);
      void qc.invalidateQueries({ queryKey: ["config"] });
    },
  });
}

// --- calendar feed (PLAN Phase 7a) ------------------------------------------------

export const useCalendarFeed = () =>
  useQuery({
    queryKey: ["settings", "calendar"],
    queryFn: () => call(client.GET("/api/v1/settings/calendar")),
  });

export function useRotateCalendarFeed() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => call(client.POST("/api/v1/settings/calendar/rotate")),
    onSuccess: (data) => qc.setQueryData(["settings", "calendar"], data),
  });
}

// --- iCloud calendars (PLAN Phase 7b) ----------------------------------------------

export type ICloud = Schemas["ICloudOut"];
export type Conflict = Schemas["ConflictOut"];

export const useICloud = () =>
  useQuery({
    queryKey: ["settings", "icloud"],
    queryFn: () => call(client.GET("/api/v1/settings/icloud")),
    // Follow a sync that is running in the background (e.g. right after connecting).
    refetchInterval: (q) => (q.state.data?.status.running ? 1500 : false),
  });

function useICloudWrite<A>(fn: (args: A) => Promise<ICloud>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (data) => {
      qc.setQueryData(["settings", "icloud"], data);
      for (const key of [["events"], ["today"], ["conflicts"]]) {
        void qc.invalidateQueries({ queryKey: key });
      }
    },
  });
}

export const useConnectICloud = () =>
  useICloudWrite((body: Schemas["ICloudLogin"]) =>
    call(client.PUT("/api/v1/settings/icloud", { body })),
  );

export const useDisconnectICloud = () =>
  useICloudWrite(() => call(client.DELETE("/api/v1/settings/icloud")));

export const useSyncCalendars = () =>
  useICloudWrite(() => call(client.POST("/api/v1/calendar/sync")));

export const useCalendarChannel = () =>
  useICloudWrite((body: Schemas["CalendarChannelIn"]) =>
    call(client.PUT("/api/v1/settings/icloud/channels", { body })),
  );

export const useCalendarConflicts = () =>
  useQuery({
    queryKey: ["conflicts"],
    queryFn: () => call(client.GET("/api/v1/calendar/conflicts")),
  });

export const useResolveConflict = () =>
  useWrite("event", ({ id, keep }: { id: string; keep: "app" | "calendar" }) =>
    call(
      client.POST("/api/v1/calendar/conflicts/{link_id}", {
        params: { path: { link_id: id } },
        body: { keep },
      }),
    ),
  );

// --- Obsidian vault (PLAN Phase 8) ------------------------------------------------

export type Note = Schemas["NoteOut"];
export type NoteDetail = Schemas["NoteDetailOut"];
export type Material = Schemas["MaterialOut"];
export type VaultSettings = Schemas["VaultOut"];

export const useVaultSettings = () =>
  useQuery({
    queryKey: ["settings", "vault"],
    queryFn: () => call(client.GET("/api/v1/settings/vault")),
    refetchInterval: (q) => (q.state.data?.status.running ? 1500 : false),
  });

function useVaultWrite<A>(fn: (args: A) => Promise<VaultSettings>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (data) => {
      qc.setQueryData(["settings", "vault"], data);
      for (const key of [["notes"], ["materials"], ["tasks"], ["vault"]]) {
        void qc.invalidateQueries({ queryKey: key });
      }
    },
  });
}

export const useSaveVault = () =>
  useVaultWrite((body: Schemas["VaultIn"]) =>
    call(client.PUT("/api/v1/settings/vault", { body })),
  );

export const useSyncVault = () =>
  useVaultWrite(() => call(client.POST("/api/v1/vault/sync")));

export const useVaultFolders = (enabled: boolean) =>
  useQuery({
    queryKey: ["vault", "folders"],
    enabled,
    queryFn: () => call(client.GET("/api/v1/vault/folders")),
    retry: false,
  });

export type NoteSort = "relevance" | "modified" | "title" | "path";
export type SortOrder = "asc" | "desc";

export const useNotes = (
  channelId: string | undefined,
  q: string,
  sort?: NoteSort,
  order?: SortOrder,
) =>
  useQuery({
    queryKey: ["notes", channelId ?? "all", q, sort, order],
    queryFn: () =>
      call(
        client.GET("/api/v1/notes", {
          params: {
            query: { channel_id: channelId, q: q || undefined, sort, order },
          },
        }),
      ),
    placeholderData: (previous) => previous,
  });

export const useNote = (noteId: string | null) =>
  useQuery({
    queryKey: ["notes", "detail", noteId],
    enabled: noteId !== null,
    queryFn: () =>
      call(
        client.GET("/api/v1/notes/{note_id}", {
          params: { path: { note_id: noteId ?? "" } },
        }),
      ),
  });

export const useMaterials = (channelId: string) =>
  useQuery({
    queryKey: ["materials", channelId],
    queryFn: () =>
      call(
        client.GET("/api/v1/channels/{channel_id}/materials", {
          params: { path: { channel_id: channelId } },
        }),
      ),
  });

export const vaultFileUrl = (path: string) =>
  `/api/v1/vault/file?path=${encodeURIComponent(path)}`;

// --- plan usage (PLAN Phase 9) ------------------------------------------------------

export type Usage = Schemas["UsageOut"];
export type ProviderUsage = Schemas["ProviderUsageOut"];

export const useUsage = () =>
  useQuery({
    queryKey: ["usage"],
    queryFn: () => call(client.GET("/api/v1/usage")),
    refetchInterval: 60_000, // "⏱ 2h13m" counts down; the values come by WebSocket
  });

function useUsageWrite(fn: () => Promise<Usage>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (data) => qc.setQueryData(["usage"], data),
  });
}

export const useRefreshUsage = () =>
  useUsageWrite(() => call(client.POST("/api/v1/usage/refresh")));

// --- custom agents and debates (PLAN Phase 10) -------------------------------------

export type AgentTool = Schemas["ToolOut"];
export type AgentForm = Schemas["AgentIn"];
export type Debate = Schemas["DebateOut"];

export const useAgentTools = () =>
  useQuery({
    queryKey: ["agents", "tools"],
    queryFn: () => call(client.GET("/api/v1/agents/tools")),
    staleTime: Number.POSITIVE_INFINITY,
  });

/** `editing`: the @name of the agent being changed; without it a new one is made. */
export const useSaveAgent = () =>
  useWrite("agent", ({ editing, ...body }: AgentForm & { editing?: string }) =>
    editing
      ? call(
          client.PATCH("/api/v1/agents/{name}", {
            params: { path: { name: editing } },
            body,
          }),
        )
      : call(client.POST("/api/v1/agents", { body })),
  );

export const useDeleteAgent = () =>
  useWrite("agent", (name: string) =>
    call(
      client.DELETE("/api/v1/agents/{name}", { params: { path: { name } } }),
    ),
  );

export const useImportAgent = () =>
  useWrite("agent", (yaml: string) =>
    call(client.POST("/api/v1/agents/import", { body: { yaml } })),
  );

export const agentExportUrl = (name: string) =>
  `/api/v1/agents/${encodeURIComponent(name)}/export`;

const debatePath = (id: string) => ({ params: { path: { debate_id: id } } });

export const useCancelDebate = () =>
  useWrite("debate", (id: string) =>
    call(client.POST("/api/v1/debates/{debate_id}/cancel", debatePath(id))),
  );

export const useDebateToTask = () =>
  useWrite(["debate", "task"], (id: string) =>
    call(client.POST("/api/v1/debates/{debate_id}/task", debatePath(id))),
  );

export const useDebateToNote = () =>
  useWrite(["debate", "note_ref"], (id: string) =>
    call(client.POST("/api/v1/debates/{debate_id}/note", debatePath(id))),
  );

export const useAgentModels = (backend: string) =>
  useQuery({
    queryKey: ["agents", "models", backend],
    queryFn: () =>
      call(
        client.GET("/api/v1/agents/models", {
          params: { query: { backend: backend as Agent["backend"] } },
        }),
      ),
    staleTime: 10 * 60_000, // listing starts the CLI; the server caches too
  });

// --- notices, review, operations (PLAN Phase 11) -----------------------------------

export type Notice = Schemas["NotificationOut"];
export type NotifySettings = Schemas["NotifySettingsOut"];
export type Ops = Schemas["OpsOut"];

export const useNotifications = () =>
  useQuery({
    queryKey: ["notifications"],
    queryFn: () => call(client.GET("/api/v1/notifications")),
  });

export const useReadNotification = () =>
  useWrite("notification", (id: string) =>
    call(
      client.POST("/api/v1/notifications/{notification_id}/read", {
        params: { path: { notification_id: id } },
      }),
    ),
  );

export const useReadAllNotifications = () =>
  useWrite("notification", () =>
    call(client.POST("/api/v1/notifications/read-all")),
  );

export const useNotifySettings = () =>
  useQuery({
    queryKey: ["settings", "notify"],
    queryFn: () => call(client.GET("/api/v1/settings/notify")),
  });

export function useSaveNotify() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Schemas["NotifySettingsIn"]) =>
      call(client.PUT("/api/v1/settings/notify", { body })),
    onSuccess: (data) => qc.setQueryData(["settings", "notify"], data),
  });
}

export const useNotifyTargets = (enabled: boolean) =>
  useQuery({
    queryKey: ["notify", "targets"],
    enabled,
    queryFn: () => call(client.GET("/api/v1/notify/targets")),
    staleTime: 5 * 60_000,
  });

export const useTestNotify = () =>
  useMutation({
    mutationFn: (target: string) =>
      call(client.POST("/api/v1/notify/test", { body: { target, label: "" } })),
  });

export const useMakeReview = () =>
  useWrite("message", () => call(client.POST("/api/v1/review/weekly")));

export const useAnalytics = () =>
  useQuery({
    queryKey: ["analytics"],
    queryFn: () => call(client.GET("/api/v1/analytics")),
  });

export const useOps = () =>
  useQuery({
    queryKey: ["ops"],
    queryFn: () => call(client.GET("/api/v1/ops")),
  });

function useOpsWrite<A>(fn: (args: A) => Promise<Ops>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (data) => qc.setQueryData(["ops"], data),
  });
}

export const useBackupNow = () =>
  useOpsWrite(() => call(client.POST("/api/v1/ops/backup")));
export const useBackupKeep = () =>
  useOpsWrite((keep: number) =>
    call(client.PUT("/api/v1/ops/backup", { body: { keep } })),
  );
export const useInstallService = () =>
  useOpsWrite((startNow: boolean) =>
    call(client.POST("/api/v1/ops/service", { body: { start_now: startNow } })),
  );
export const useUninstallService = () =>
  useOpsWrite(() => call(client.DELETE("/api/v1/ops/service")));

// --- first-run setup (PLAN Phase 12) ------------------------------------------------

export type Setup = Schemas["SetupOut"];
export type SetupTool = Schemas["SetupToolOut"];

export const useSetup = () =>
  useQuery({
    queryKey: ["setup"],
    queryFn: () => call(client.GET("/api/v1/setup")),
    staleTime: Number.POSITIVE_INFINITY, // tool checks run CLIs; refetch on purpose
  });

function useToolWrite<A extends { name: string }>(
  fn: (args: A) => Promise<SetupTool>,
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (tool) => {
      qc.setQueryData<Setup>(["setup"], (old) =>
        old
          ? {
              ...old,
              tools: old.tools.map((t) => (t.name === tool.name ? tool : t)),
            }
          : old,
      );
      void qc.invalidateQueries({ queryKey: ["agents"] });
    },
  });
}

/** Registers Argos' MCP server and installs the argos skill in the tool. */
export const useConnectTool = () =>
  useToolWrite(({ name }: { name: string }) =>
    call(
      client.POST("/api/v1/setup/tools/{name}/connect", {
        params: { path: { name } },
      }),
    ),
  );

/** Removes Argos' MCP entry and/or skill from the tool. */
export const useDisconnectTool = () =>
  useToolWrite(
    ({ name, mcp, skill }: { name: string; mcp: boolean; skill: boolean }) =>
      call(
        client.POST("/api/v1/setup/tools/{name}/disconnect", {
          params: { path: { name } },
          body: { mcp, skill },
        }),
      ),
  );

export function useFinishSetup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => call(client.POST("/api/v1/setup/done")),
    onSuccess: (data) => qc.setQueryData(["setup"], data),
  });
}

/** Which model a built-in (or custom) agent answers with; null = the tool's default. */
export function useSetAgentModel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, model }: { name: string; model: string | null }) =>
      call(
        client.PUT("/api/v1/agents/{name}/model", {
          params: { path: { name } },
          body: { model },
        }),
      ),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["agents"] }),
  });
}

/** Uninstall, first step: takes Argos out of the agent tools, login items and Keychain. */
export const usePrepareUninstall = () =>
  useMutation({
    mutationFn: () => call(client.POST("/api/v1/setup/uninstall")),
  });
