---
name: argos
description: Record and look up schedules/tasks in the Argos app
version: 1.0.0
author: argos
platforms: [macos]
metadata:
  hermes:
    tags: [Argos, Calendar, Tasks, Productivity]
    category: productivity
---

# Argos — the user's schedule and task hub

Argos is the single source of truth for the user's events, tasks and deadlines
(courses, projects, everyday life). It runs locally on the user's Mac and is
connected to you as the `argos` MCP server. Use it instead of your own memory for
anything time- or task-related, so the user sees it in the app and on their calendar.

## When to Use

- The user mentions an appointment, class, meeting, exam or anything with a time →
  record an event.
- The user mentions something to do, a deadline or homework → record a task.
- The user asks what is on today, this week, what is due, or how a course is going.
- Something worth keeping is said in passing but it is unclear what it is → capture it
  to the Argos inbox and let the user sort it.

Do **not** save these to `memory` / USER.md / MEMORY.md. Memory is for preferences and
facts about the user, not for their schedule.

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
5. If Argos is unreachable, say so and offer to capture it later; do not silently fall
   back to memory.
