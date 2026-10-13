/** The channels list after `ids` (a sidebar section, in its new order) take the
 * slots they held before; the server applies the same rule (PUT /channels/order). */
export function placeInSlots<T extends { id: string }>(
  all: T[],
  ids: string[],
): T[] {
  const wanted = new Set(ids);
  const byId = new Map(all.map((c) => [c.id, c]));
  const moved = ids.map((id) => byId.get(id)).filter((c) => c !== undefined);
  let next = 0;
  return all.map((c) => (wanted.has(c.id) ? (moved[next++] ?? c) : c));
}
