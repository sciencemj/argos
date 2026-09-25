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
    event: [["events"], ["today"], ...feeds],
    inbox_item: [["inbox"], ["today"], ...feeds],
    message: feeds,
    routine: [["routines"]],
    approval: [["approvals"], ...feeds],
    agent_run: [["tasks"], ["task"], ...feeds], // job status lives on the card
    routine_check: [["routines"]],
    channel: [["channels"], ["tasks"]],
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
export const useMessages = (channelId: string) =>
  useInfiniteQuery({
    queryKey: ["messages", channelId],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      call(
        client.GET("/api/v1/channels/{channel_id}/messages", {
          params: {
            path: { channel_id: channelId },
            query: pageParam ? { cursor: pageParam } : {},
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
