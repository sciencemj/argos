/** Bridge to the desktop app (PLAN Phase 12). The Tauri shell exposes a few commands to
 * pages served from its own server; in a browser (or on the iPad) there is no bridge. */
type Tauri = {
  core: {
    invoke: (cmd: string, args?: Record<string, unknown>) => Promise<unknown>;
  };
};

const tauri = (): Tauri | undefined =>
  (window as unknown as { __TAURI__?: Tauri }).__TAURI__;

export const inDesktopApp = () => tauri() !== undefined;

function invoke(cmd: string, args?: Record<string, unknown>) {
  return tauri()
    ?.core.invoke(cmd, args)
    .catch(() => undefined);
}

/** A macOS notification (WKWebView has no web Notification). */
export const desktopNotify = (title: string, body?: string) =>
  invoke("notify", { title, body: body ?? "" });

/** Hides the quick-capture window. */
export const hideQuickWindow = () => invoke("hide_quick");

/** Brings the main window forward at a path (e.g. the inbox after capturing). */
export const openMainWindow = (path: string) => invoke("open_main", { path });

/** Opens a chat attachment in its default app (Preview, …); the app copies it out first. */
export const openAttachment = (id: string, name: string) =>
  invoke("open_attachment", { id, name });

export type UpdateState = {
  current: string;
  checking: boolean;
  available: string | null;
  ready: boolean;
  error: string | null;
  checked_at: number | null;
};

/** The app's version and the automatic update's progress (src-tauri/src/update.rs). */
export const updateStatus = () =>
  invoke("update_status") as Promise<UpdateState | undefined> | undefined;
export const checkUpdate = () =>
  invoke("check_update") as Promise<UpdateState | undefined> | undefined;
export const restartToUpdate = () => invoke("restart_to_update");

/** Uninstall, last step: the app moves itself (and the data, if asked) to the Trash
 * and quits. Resolves with an error message if something could not be moved. */
export const uninstallApp = (deleteData: boolean) =>
  tauri()
    ?.core.invoke("uninstall", { deleteData })
    .then(() => null)
    .catch((e: unknown) => String(e));
