import {
  type QueryClient,
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
  const keys: Record<string, string[][]> = {
    task: [["tasks"], ["today"], ["task"], ["activity"]],
    event: [["events"], ["today"]],
    inbox_item: [["inbox"], ["today"]],
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

// --- mutations ------------------------------------------------------------------

function useWrite<A, R>(objectType: string, fn: (args: A) => Promise<R>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSettled: () => invalidateFor(qc, objectType),
  });
}

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
