---
name: argos
description: Use the Argos MCP tools (argos server) to record and look up the user's events, tasks, deadlines, inbox notes and Obsidian notes. Use when the user mentions a schedule, appointment, deadline, homework or task, asks what is due or on today, or wants to capture a note.
---

# Argos — the user's schedule and task hub

Argos is the single source of truth for the user's events, tasks and deadlines
(courses, projects, everyday life). It runs on the user's Mac and is connected to you
as the `argos` MCP server (installed by the Argos app: 설정 → 에이전트 도구).

## When to Use

- The user mentions an appointment, class, meeting, exam or anything with a time →
  record an event.
- The user mentions something to do, a deadline or homework → record a task.
- The user asks what is on today, this week, what is due, or how a course is going.
- Something worth keeping is said in passing but it is unclear what it is → capture it
  to the Argos inbox and let the user sort it.

Keep schedules and tasks in Argos, not in notes files or your own memory, so the user
sees them in the app and on their calendar.

## Tools (argos MCP server)

| Need | Tool |
|---|---|
| Channel names (courses, projects, `일상`) | `list_channels` |
| Today: events, due tasks, inbox, routines | `get_today` |
| Events in a range | `get_schedule(start, end, channel?)` |
| Tasks | `list_tasks(channel?, status?, include_done?)` |
| Per-course progress | `get_course_progress(channel?)` |
| The user's Obsidian notes (to ground answers) | `search_notes(query, channel?)` |
| New task | `add_task(title, channel, due?, description?, priority?)` |
| New event | `create_event(title, channel, start, end?, location?)` |
| Change task / move on the kanban | `update_task`, `move_task(task_id, status)` |
| Change event | `update_event` |
| Unsorted note | `capture_note(text, channel?)` |
| Delete (needs approval) | `delete_task`, `delete_event` |

## Rules

1. **Channel**: call `list_channels` if unsure. Course or project matters go to that
   channel; everyday things (errands, appointments, exercise) go to `일상`.
2. **Time**: the user's local time zone. Dates as `YYYY-MM-DD` (a task due that day gets 23:59; an event
   on a date only becomes all-day) or ISO datetimes like `2026-09-29T15:00`. Resolve
   relative dates ("다음주 화요일") against today's date before calling the tool.
3. **Deleting** only creates an approval request. Tell the user it waits for their
   approval in Argos; never claim it is deleted.
4. **Confirm briefly** after recording: title, date/time and channel in one line.
5. If Argos is unreachable, say so and offer to capture it later; do not silently keep it
   somewhere else.
