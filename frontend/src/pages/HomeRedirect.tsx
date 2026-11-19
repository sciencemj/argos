import { Navigate } from "react-router";
import { useChannels } from "../api";
import { lastChannel } from "../lastChannel";
import { TodayPage } from "./TodayPage";

/** `/` (v0.2): back to the channel last open, else the first course or project; with
 * none yet, the Today overview (which also stays at /today in the sidebar). */
export function HomeRedirect() {
  const channels = useChannels();
  if (channels.isLoading) return null;
  const list = channels.data?.channels ?? [];
  const last = lastChannel();
  const target =
    list.find((c) => c.id === last && c.kind !== "dm") ??
    list.find((c) => c.kind === "course" || c.kind === "project");
  return target ? <Navigate to={`/c/${target.id}`} replace /> : <TodayPage />;
}
