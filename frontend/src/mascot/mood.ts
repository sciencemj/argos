import { t } from "../i18n";

/** How Argos, the dog on the Today page, feels about the day. Each mood is a looping
 * clip in argos-dog.glb; Greet and Jump are one-shots played on top. */
export type Mood = "idle" | "happy" | "sad" | "curious";

/** Open inbox items at which the dog starts tilting its head at them. */
export const INBOX_PILE = 5;

export const MOOD_CLIP: Record<Mood, string> = {
  idle: "Idle",
  happy: "Happy",
  sad: "Sad",
  curious: "Curious",
};
export const GREET_CLIP = "Greet";
export const JUMP_CLIP = "Jump";

export interface MoodInput {
  overdue: number;
  dueToday: number;
  inbox: number;
  doneThisWeek: number;
}

/** A passed deadline outweighs everything, then an inbox that needs sorting; a clear
 * day after some finished work is a happy one. */
export function deriveMood({
  overdue,
  dueToday,
  inbox,
  doneThisWeek,
}: MoodInput): Mood {
  if (overdue > 0) return "sad";
  if (inbox >= INBOX_PILE) return "curious";
  if (dueToday === 0 && doneThisWeek > 0) return "happy";
  return "idle";
}

export function countOverdue(
  tasks: readonly { status: string; due_at?: string | null }[],
  now: Date,
): number {
  const at = now.getTime();
  return tasks.filter(
    (x) => x.status !== "done" && x.due_at && new Date(x.due_at).getTime() < at,
  ).length;
}

export function moodLabel(mood: Mood): string {
  switch (mood) {
    case "sad":
      return t(
        "마감이 지난 일이 있어 시무룩해요",
        "Moping over a missed deadline",
      );
    case "curious":
      return t(
        "인박스가 쌓여서 갸웃거려요",
        "Puzzled by the pile in the inbox",
      );
    case "happy":
      return t("오늘 할 일이 끝나서 신났어요", "Happy, today's work is done");
    default:
      return t("곁에서 기다리는 중", "Keeping you company");
  }
}
