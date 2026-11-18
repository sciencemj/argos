"""Synthetic multi-turn SFT & evaluation dataset generator for Argos native tools.

Uses the exact OpenAI tool schemas exported by `argos.sft_export.tool_specs()`
(`add_task`, `update_task`, `move_task`, `delete_task`, `create_event`, `update_event`,
`delete_event`, `list_channels`, `get_today`, `get_schedule`, `search_notes`,
`search_course_lms`, `search_knowledge`, `list_tasks`, `list_inbox`, `get_course_progress`,
`create_channel`, `capture_note`, `read_file`, `list_dir`, `glob_files`, `grep_files`,
`write_file`, `edit_file`, `delete_file`, `run_command`) to synthesize multi-turn
trajectories in the OpenAI `messages + tools` JSONL format consumed by `train_sft.py`
and `eval_harness.py`.

Usage:
    uv run --project backend python training/generate_seed_data.py --out-dir training/data
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BACKEND_SRC = ROOT / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from argos.sft_export import SYSTEM_PROMPT, split_of, tool_specs  # noqa: E402

COURSES = [
    ("알고리즘", "algorithm", "CS301"),
    ("운영체제", "os", "CS302"),
    ("기계학습", "ml", "CS401"),
    ("데이터베이스", "db", "CS304"),
    ("컴퓨터네트워크", "net", "CS305"),
    ("소프트웨어공학", "swe", "CS306"),
]

PROJECTS = [
    ("졸업프로젝트", "grad-proj"),
    ("Argos개발", "argos-dev"),
    ("연구실세미나", "lab-seminar"),
]

TASK_TITLES = [
    "동적계획법 연습문제 5번 풀이",
    "가상 메모리 페이지 교체 시뮬레이터 구현",
    "중간고사 범위 요약 노트 정리",
    "로지스틱 회귀 미니 프로젝트 제출",
    "B+Tree 인덱스 구현 과제",
    "TCP 혼잡 제어 실험 보고서 작성",
    "PR 코드 리뷰 및 단위 테스트 보강",
    "논문 관련 연구 조사 발표 슬라이드 초안",
]

LOCATIONS = [
    "공학관 302호",
    "제1실습실 (401호)",
    "중앙도서관 그룹스터디룸 B",
    "온라인 Zoom",
    "연구실 512호",
]

DATES = [
    "2026-11-19",
    "2026-11-20",
    "2026-11-23",
    "2026-11-25",
    "2026-11-27",
    "2026-11-30",
    "2026-12-02",
    "2026-12-04",
]


def _call_msg(call_id: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(args, ensure_ascii=False),
                },
            }
        ],
    }


def _tool_msg(call_id: str, name: str, output: Any) -> dict[str, Any]:
    text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "name": name,
        "content": text,
    }


def _scenario_add_and_update_task(rng: random.Random, idx: int) -> dict[str, Any]:
    course, _, _ = rng.choice(COURSES)
    title = rng.choice(TASK_TITLES)
    due1, due2 = rng.sample(DATES, 2)
    prio = rng.choice([1, 2, 3])
    task_id = f"task_{idx:04d}"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"{course} 채널에 '{title}' 할 일을 마감일 {due1}, 우선순위 {prio}으로 추가해줘."
            ),
        },
        _call_msg(
            f"call_{idx}_1",
            "add_task",
            {"title": title, "channel": course, "due": due1, "priority": prio},
        ),
        _tool_msg(
            f"call_{idx}_1",
            "add_task",
            {
                "id": task_id,
                "title": title,
                "channel": course,
                "status": "todo",
                "due_at": f"{due1}T23:59:00+09:00",
                "priority": prio,
                "description": None,
            },
        ),
        {
            "role": "assistant",
            "content": (
                f"#{course} 채널에 '{title}' 할 일(마감 {due1}, 우선순위 {prio})을 추가했어요."
            ),
        },
        {
            "role": "user",
            "content": (
                f"방금 만든 할 일 마감일을 {due2}로 늦추고 진행 중(in_progress) 상태로 옮겨줘."
            ),
        },
        _call_msg(
            f"call_{idx}_2",
            "update_task",
            {"task_id": task_id, "due": due2},
        ),
        _tool_msg(
            f"call_{idx}_2",
            "update_task",
            {
                "id": task_id,
                "title": title,
                "channel": course,
                "status": "todo",
                "due_at": f"{due2}T23:59:00+09:00",
                "priority": prio,
                "description": None,
            },
        ),
        _call_msg(
            f"call_{idx}_3",
            "move_task",
            {"task_id": task_id, "status": "in_progress"},
        ),
        _tool_msg(
            f"call_{idx}_3",
            "move_task",
            {
                "id": task_id,
                "title": title,
                "channel": course,
                "status": "in_progress",
                "due_at": f"{due2}T23:59:00+09:00",
                "priority": prio,
                "description": None,
            },
        ),
        {
            "role": "assistant",
            "content": (
                f"'{title}' 할 일의 마감일을 {due2}로 수정하고 "
                "상태를 진행 중(in_progress)으로 변경했습니다."
            ),
        },
    ]
    return {"scenario": "add_and_update_task", "messages": messages}


def _scenario_create_event_and_schedule(rng: random.Random, idx: int) -> dict[str, Any]:
    course, _, _ = rng.choice(COURSES)
    day = rng.choice(DATES)
    hour = rng.choice([10, 13, 14, 15, 16])
    loc = rng.choice(LOCATIONS)
    start_iso = f"{day}T{hour:02d}:00:00+09:00"
    end_iso = f"{day}T{hour + 2:02d}:00:00+09:00"
    ev_id = f"ev_{idx:04d}"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"{day} 일정 먼저 확인하고, 비어 있으면 {course} 팀 미팅을 "
                f"{hour}시부터 2시간 동안 {loc}에 잡아줘."
            ),
        },
        _call_msg(
            f"call_{idx}_1",
            "get_schedule",
            {"start": day, "end": day, "channel": course},
        ),
        _tool_msg(f"call_{idx}_1", "get_schedule", []),
        _call_msg(
            f"call_{idx}_2",
            "create_event",
            {
                "title": f"{course} 팀 미팅",
                "channel": course,
                "start": start_iso,
                "end": end_iso,
                "location": loc,
            },
        ),
        _tool_msg(
            f"call_{idx}_2",
            "create_event",
            {
                "id": ev_id,
                "title": f"{course} 팀 미팅",
                "channel": course,
                "all_day": False,
                "starts_at": start_iso,
                "ends_at": end_iso,
                "location": loc,
            },
        ),
        {
            "role": "assistant",
            "content": (
                f"{day} #{course} 채널에 다른 일정이 없어 {hour}:00~{hour + 2}:00에 "
                f"'{course} 팀 미팅'({loc}) 일정을 등록했어요."
            ),
        },
    ]
    return {"scenario": "create_event_and_schedule", "messages": messages}


def _scenario_search_knowledge_and_read_file(rng: random.Random, idx: int) -> dict[str, Any]:
    course, slug, code = rng.choice(COURSES)
    topic = rng.choice(
        ["Red-Black Tree", "Page Table", "Gradient Descent", "B-Tree", "Raft Consensus"]
    )
    rel_path = f"uploads/{slug}/{code}_lecture_notes.md"
    due = rng.choice(DATES)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"{course} 채널 자료에서 '{topic}' 관련 내용을 찾아서 요약해 주고, "
                f"{due}까지 복습 할 일도 추가해줘."
            ),
        },
        _call_msg(
            f"call_{idx}_1",
            "search_knowledge",
            {"query": topic, "channel": course, "limit": 5},
        ),
        _tool_msg(
            f"call_{idx}_1",
            "search_knowledge",
            [
                {
                    "title": f"{code} 강의노트 - {topic}",
                    "source": "upload",
                    "channel": course,
                    "path": rel_path,
                    "snippet": f"...핵심 개념: [{topic}]의 시간 복잡도와 불변성(invariant) 정리...",
                }
            ],
        ),
        _call_msg(
            f"call_{idx}_2",
            "read_file",
            {"path": rel_path, "offset": 1, "limit": 40},
        ),
        _tool_msg(
            f"call_{idx}_2",
            "read_file",
            (
                f"     1\t# {topic} 정리\n"
                "     2\t- 핵심 원리 및 불변 조건 설명\n"
                "     3\t- 최악 시간 복잡도 보장 및 응용 사례"
            ),
        ),
        _call_msg(
            f"call_{idx}_3",
            "add_task",
            {
                "title": f"{topic} 강의노트 복습",
                "channel": course,
                "due": due,
                "description": f"{rel_path} 기반 핵심 원리 및 불변 조건 복습",
            },
        ),
        _tool_msg(
            f"call_{idx}_3",
            "add_task",
            {
                "id": f"task_{idx:04d}",
                "title": f"{topic} 강의노트 복습",
                "channel": course,
                "status": "todo",
                "due_at": f"{due}T23:59:00+09:00",
                "priority": None,
                "description": f"{rel_path} 기반 핵심 원리 및 불변 조건 복습",
            },
        ),
        {
            "role": "assistant",
            "content": (
                f"`{rel_path}` 문서에서 **{topic}**의 핵심 원리와 불변 조건 내용을 확인했어요. "
                f"아울러 #{course} 채널에 {due} 마감으로 "
                f"'{topic} 강의노트 복습' 할 일을 등록했습니다."
            ),
        },
    ]
    return {"scenario": "search_knowledge_and_read_file", "messages": messages}


def _scenario_workspace_edit_and_run_command(rng: random.Random, idx: int) -> dict[str, Any]:
    proj, slug = rng.choice(PROJECTS)
    target_file = f"src/{slug.replace('-', '_')}/solver.py"
    old_line = "MAX_ITER = 100"
    new_line = "MAX_ITER = 500"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"`{target_file}` 파일을 읽어서 `{old_line}`을 `{new_line}`으로 "
                "수정하고 pytest로 테스트를 돌려줘."
            ),
        },
        _call_msg(
            f"call_{idx}_1",
            "read_file",
            {"path": target_file, "offset": 1, "limit": 50},
        ),
        _tool_msg(
            f"call_{idx}_1",
            "read_file",
            (
                f'     1\t"""{proj} solver module."""\n'
                f"     2\t{old_line}\n"
                "     3\t\n"
                "     4\tdef solve(x: int) -> int:\n"
                "     5\t    return min(x, MAX_ITER)"
            ),
        ),
        _call_msg(
            f"call_{idx}_2",
            "edit_file",
            {"path": target_file, "old_string": old_line, "new_string": new_line},
        ),
        _tool_msg(
            f"call_{idx}_2",
            "edit_file",
            f"{target_file}: 1곳을 바꿨어요 (원본은 백업해 뒀어요)",
        ),
        _call_msg(
            f"call_{idx}_3",
            "run_command",
            {"command": "pytest -q"},
        ),
        _tool_msg(
            f"call_{idx}_3",
            "run_command",
            "exit code 0\n4 passed in 0.12s",
        ),
        {
            "role": "assistant",
            "content": (
                f"`{target_file}`에서 `{old_line}`을 `{new_line}`으로 수정했고, "
                "`pytest -q` 실행 결과 4개 테스트가 모두 통과했어요."
            ),
        },
    ]
    return {"scenario": "workspace_edit_and_run_command", "messages": messages}


def _scenario_lms_and_today_review(rng: random.Random, idx: int) -> dict[str, Any]:
    course, _, code = rng.choice(COURSES)
    due = rng.choice(DATES)
    hw_title = f"{code} 과제 #{rng.randint(1, 5)}"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"오늘 할 일 현황이랑 {course} 과목의 최신 LMS 과제 공지를 "
                "확인해서 아직 할 일에 없으면 추가해줘."
            ),
        },
        _call_msg(f"call_{idx}_1", "get_today", {}),
        _tool_msg(
            f"call_{idx}_1",
            "get_today",
            {
                "date": "2026-11-18",
                "events": [],
                "due_tasks": [],
                "open_inbox": 1,
                "routines": [{"title": "알고리즘 1문제 풀기", "done": False, "streak": 4}],
            },
        ),
        _call_msg(
            f"call_{idx}_2",
            "search_course_lms",
            {"query": "과제", "channel": course, "kind": "assignment", "limit": 5},
        ),
        _tool_msg(
            f"call_{idx}_2",
            "search_course_lms",
            [
                {
                    "kind": "assignment",
                    "title": hw_title,
                    "channel": course,
                    "date": f"{due}T23:59:00+09:00",
                    "url": f"https://lms.example.edu/courses/{code}/assignments/1",
                    "summary": f"{hw_title} 제출 안내 (마감: {due})",
                    "text": f"제출 기한: {due} 23:59까지 PDF 및 소스코드 업로드.",
                }
            ],
        ),
        _call_msg(
            f"call_{idx}_3",
            "add_task",
            {
                "title": hw_title,
                "channel": course,
                "due": due,
                "priority": 2,
            },
        ),
        _tool_msg(
            f"call_{idx}_3",
            "add_task",
            {
                "id": f"task_{idx:04d}",
                "title": hw_title,
                "channel": course,
                "status": "todo",
                "due_at": f"{due}T23:59:00+09:00",
                "priority": 2,
                "description": None,
            },
        ),
        {
            "role": "assistant",
            "content": (
                f"오늘 마감 임박 할 일은 비어 있었고, LMS에서 확인한 **{hw_title}** "
                f"(마감 {due}) 공지를 #{course} 채널 할 일에 우선순위 2로 추가했어요."
            ),
        },
    ]
    return {"scenario": "lms_and_today_review", "messages": messages}


def _scenario_grep_and_write_summary(rng: random.Random, idx: int) -> dict[str, Any]:
    proj, slug = rng.choice(PROJECTS)
    pattern = rng.choice(["TODO", "FIXME", "def evaluate"])
    out_note = f"notes/{slug}_todo_report.md"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"작업 폴더에서 `{pattern}` 패턴을 검색해서 결과를 `{out_note}` 파일로 "
                f"저장하고, {proj} 채널의 진행 상황도 알려줘."
            ),
        },
        _call_msg(
            f"call_{idx}_1",
            "grep_files",
            {"pattern": pattern, "glob": "*.py", "max_results": 20},
        ),
        _tool_msg(
            f"call_{idx}_1",
            "grep_files",
            (
                f"src/main.py:14: # {pattern}: handle timeout edge case\n"
                f"src/utils.py:42: # {pattern}: optimize batch size"
            ),
        ),
        _call_msg(
            f"call_{idx}_2",
            "write_file",
            {
                "path": out_note,
                "content": (
                    f"# {proj} `{pattern}` 점검 목록\n\n- `src/main.py:14`\n- `src/utils.py:42`\n"
                ),
            },
        ),
        _tool_msg(
            f"call_{idx}_2",
            "write_file",
            f"{out_note}에 84 bytes를 썼어요",
        ),
        _call_msg(
            f"call_{idx}_3",
            "get_course_progress",
            {"channel": proj},
        ),
        _tool_msg(
            f"call_{idx}_3",
            "get_course_progress",
            [
                {
                    "channel": proj,
                    "kind": "project",
                    "counts": {
                        "backlog": 1,
                        "todo": 2,
                        "in_progress": 1,
                        "review": 0,
                        "done": 5,
                    },
                    "next_deadline": {
                        "title": "중간 발표 리허설",
                        "due_at": "2026-11-25T18:00:00+09:00",
                    },
                }
            ],
        ),
        {
            "role": "assistant",
            "content": (
                f"`{pattern}` 검색 결과 2건을 `{out_note}`에 저장했어요. "
                f"현재 #{proj} 채널은 할 일 2개, 진행 중 1개, 완료 5개이며 "
                "다음 마감은 '중간 발표 리허설'(2026-11-25)입니다."
            ),
        },
    ]
    return {"scenario": "grep_and_write_summary", "messages": messages}


# --- coverage scenarios (v0.2 review): unused tools, recovery, refusal, no-tool turns ---
# Tool outputs mirror what the harness really returns: errors are "오류: <Type>: …",
# a rejected approval is the harness's exact sentence.

REJECTED = "사용자가 이 실행을 거절했어요. 다른 방법을 찾으세요."


def _scenario_explore_workspace(rng: random.Random, idx: int) -> dict[str, Any]:
    """list_dir → glob_files → read_file: finding a file before reading it."""
    proj, slug = rng.choice(PROJECTS)
    ext = rng.choice(["md", "py", "txt"])
    files = [f"notes/week{n}.{ext}" for n in range(1, 4)]
    target = files[-1]
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"{proj} 작업 폴더에 뭐가 있는지 보고 제일 최근 주차 노트 열어줘.",
        },
        _call_msg(f"call_{idx}_1", "list_dir", {"path": "."}),
        _tool_msg(
            f"call_{idx}_1", "list_dir", f"notes/\nuploads/\nREADME.md\t{rng.randint(200, 900)}"
        ),
        _call_msg(f"call_{idx}_2", "glob_files", {"pattern": f"*.{ext}", "path": "notes"}),
        _tool_msg(f"call_{idx}_2", "glob_files", "\n".join(files)),
        _call_msg(f"call_{idx}_3", "read_file", {"path": target}),
        _tool_msg(
            f"call_{idx}_3",
            "read_file",
            f"     1\t# {slug} week3\n"
            "     2\t- 남은 일: 데모 영상 편집\n"
            "     3\t- 회의: 금요일 15시",
        ),
        {
            "role": "assistant",
            "content": (
                "작업 폴더에는 notes/, uploads/, README.md가 있고, "
                f"가장 최근 노트는 `{target}`예요. "
                "남은 일은 데모 영상 편집이고 금요일 15시에 회의가 있어요."
            ),
        },
    ]
    return {"scenario": "explore_workspace", "messages": messages}


def _scenario_recover_from_error(rng: random.Random, idx: int) -> dict[str, Any]:
    """A wrong channel name fails; the agent looks up the channels and retries."""
    course, _, code = rng.choice(COURSES)
    title = rng.choice(TASK_TITLES)
    due = rng.choice(DATES)
    wrong = code  # the user names the course by its code
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"{wrong} 과목에 '{title}' 할 일 {due}까지로 넣어줘."},
        _call_msg(f"call_{idx}_1", "add_task", {"title": title, "channel": wrong, "due": due}),
        _tool_msg(f"call_{idx}_1", "add_task", f"오류: NotFoundError: channel {wrong} not found"),
        _call_msg(f"call_{idx}_2", "list_channels", {}),
        _tool_msg(
            f"call_{idx}_2",
            "list_channels",
            [
                {"id": f"ch_{n}", "name": c[0], "kind": "course", "area": "학업"}
                for n, c in enumerate(COURSES)
            ],
        ),
        _call_msg(f"call_{idx}_3", "add_task", {"title": title, "channel": course, "due": due}),
        _tool_msg(
            f"call_{idx}_3",
            "add_task",
            {
                "id": f"task_{idx:04d}",
                "title": title,
                "channel": course,
                "status": "todo",
                "due_at": f"{due}T23:59:00+09:00",
                "priority": None,
                "description": None,
            },
        ),
        {
            "role": "assistant",
            "content": f"{wrong}는 #{course} 채널이라 그쪽에 '{title}'(마감 {due})을 추가했어요.",
        },
    ]
    return {"scenario": "recover_from_error", "messages": messages}


def _scenario_delete_with_approval(rng: random.Random, idx: int) -> dict[str, Any]:
    """Destructive calls wait for the user: one rejected (and respected), one approved."""
    course, _, _ = rng.choice(COURSES)
    title = rng.choice(TASK_TITLES)
    approve = idx % 2 == 0
    task_id = f"task_{idx:04d}"
    ev_id = f"ev_{idx:04d}"
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"{course}에서 '{title}' 할 일이랑 취소된 보강 일정 지워줘."},
        _call_msg(f"call_{idx}_1", "list_tasks", {"channel": course}),
        _tool_msg(
            f"call_{idx}_1",
            "list_tasks",
            [
                {
                    "id": task_id,
                    "title": title,
                    "channel": course,
                    "status": "todo",
                    "due_at": None,
                    "priority": None,
                    "description": None,
                }
            ],
        ),
        _call_msg(f"call_{idx}_2", "delete_task", {"task_id": task_id, "reason": "사용자 요청"}),
        _tool_msg(
            f"call_{idx}_2",
            "delete_task",
            {"deleted": task_id, "title": title} if approve else REJECTED,
        ),
        _call_msg(
            f"call_{idx}_3",
            "get_schedule",
            {"start": DATES[0], "end": DATES[-1], "channel": course},
        ),
        _tool_msg(
            f"call_{idx}_3",
            "get_schedule",
            [
                {
                    "id": ev_id,
                    "title": f"{course} 보강 (취소)",
                    "channel": course,
                    "all_day": False,
                    "starts_at": f"{DATES[2]}T15:00:00+09:00",
                    "ends_at": f"{DATES[2]}T17:00:00+09:00",
                }
            ],
        ),
        _call_msg(f"call_{idx}_4", "delete_event", {"event_id": ev_id, "reason": "취소된 보강"}),
        _tool_msg(
            f"call_{idx}_4", "delete_event", {"deleted": ev_id, "title": f"{course} 보강 (취소)"}
        ),
        {
            "role": "assistant",
            "content": (
                f"'{title}' 할 일을 지웠고, "
                if approve
                else f"'{title}' 할 일 삭제는 거절하셔서 그대로 뒀어요. "
            )
            + f"'{course} 보강 (취소)' 일정은 삭제했어요.",
        },
    ]
    return {"scenario": "delete_with_approval", "messages": messages}


def _scenario_no_tool_needed(rng: random.Random, idx: int) -> dict[str, Any]:
    """Some turns need no tool: a concept question, then a clarifying question when the
    request lacks what a tool would need."""
    course, _, _ = rng.choice(COURSES)
    concept, answer = rng.choice(
        [
            (
                "시간 복잡도 O(n log n)이 뭐야?",
                "입력이 n개일 때 대략 n × log n에 비례해 시간이 늘어난다는 뜻이에요. "
                "병합 정렬이 대표적이에요.",
            ),
            (
                "페이지 폴트가 뭐야?",
                "필요한 페이지가 메모리에 없어 디스크에서 가져와야 하는 상황이에요. "
                "이때 운영체제가 개입해요.",
            ),
            (
                "과적합을 막는 방법 알려줘",
                "데이터를 늘리거나 정규화(L2, 드롭아웃)를 쓰고, "
                "검증 세트로 조기 종료하는 방법이 흔해요.",
            ),
        ]
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": concept},
        {"role": "assistant", "content": answer},
        {"role": "user", "content": "그거 관련해서 일정 하나 잡아줘."},
        {
            "role": "assistant",
            "content": (
                "어떤 일정인지, 날짜와 시간을 알려 주시겠어요? "
                f"(예: {DATES[1]} 14시 #{course} 스터디)"
            ),
        },
        {"role": "user", "content": f"{DATES[1]} 14시에 {course} 스터디 1시간."},
        _call_msg(
            f"call_{idx}_1",
            "create_event",
            {
                "title": f"{course} 스터디",
                "channel": course,
                "start": f"{DATES[1]}T14:00:00+09:00",
                "end": f"{DATES[1]}T15:00:00+09:00",
            },
        ),
        _tool_msg(
            f"call_{idx}_1",
            "create_event",
            {
                "id": f"ev_{idx:04d}",
                "title": f"{course} 스터디",
                "channel": course,
                "all_day": False,
                "starts_at": f"{DATES[1]}T14:00:00+09:00",
                "ends_at": f"{DATES[1]}T15:00:00+09:00",
            },
        ),
        {"role": "assistant", "content": f"{DATES[1]} 14:00~15:00에 #{course} 스터디를 잡았어요."},
    ]
    return {"scenario": "no_tool_needed", "messages": messages}


def _scenario_capture_and_inbox(rng: random.Random, idx: int) -> dict[str, Any]:
    """Vague notes go to the inbox (capture_note), then list_inbox."""
    idea = rng.choice(
        ["다음 주쯤 교수님께 진로 상담 메일", "졸업 요건 학점 다시 계산", "동아리 회비 정산 확인"]
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"아 맞다, {idea}… 일단 어디 적어만 둬."},
        _call_msg(f"call_{idx}_1", "capture_note", {"text": idea}),
        _tool_msg(
            f"call_{idx}_1", "capture_note", {"inbox_item_id": f"inbox_{idx:04d}", "status": "new"}
        ),
        {
            "role": "assistant",
            "content": "인박스에 적어 뒀어요. Argos가 분류 제안을 붙이면 확인해 주세요.",
        },
        {"role": "user", "content": "지금 인박스에 정리 안 된 거 몇 개야?"},
        _call_msg(f"call_{idx}_2", "list_inbox", {}),
        _tool_msg(
            f"call_{idx}_2",
            "list_inbox",
            [
                {
                    "id": f"inbox_{idx:04d}",
                    "text": idea,
                    "status": "new",
                    "suggestion": None,
                    "captured_at": f"{DATES[0]}T09:00:00+09:00",
                },
                {
                    "id": "inbox_0001",
                    "text": "도서관 책 반납",
                    "status": "suggested",
                    "suggestion": {"type": "task"},
                    "captured_at": f"{DATES[0]}T08:10:00+09:00",
                },
            ],
        ),
        {
            "role": "assistant",
            "content": f"정리 안 된 항목이 2개 있어요: '{idea}', '도서관 책 반납'.",
        },
    ]
    return {"scenario": "capture_and_inbox", "messages": messages}


def _scenario_english_notes_and_channel(rng: random.Random, idx: int) -> dict[str, Any]:
    """English user: search_notes, create_channel, update_event; answers in English."""
    topic = rng.choice(["transformer attention", "B-tree split", "TCP slow start"])
    name = rng.choice(["Reading Group", "Capstone Ops", "Paper Club"])
    ev_id = f"ev_{idx:04d}"
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Do I have notes about {topic}?"},
        _call_msg(f"call_{idx}_1", "search_notes", {"query": topic, "limit": 3}),
        _tool_msg(
            f"call_{idx}_1",
            "search_notes",
            [
                {
                    "title": f"{topic} summary",
                    "path": f"lectures/{topic.replace(' ', '-')}.md",
                    "channel": None,
                    "snippet": f"…key idea of [{topic}] is…",
                }
            ],
        ),
        {"role": "assistant", "content": f"Yes — '{topic} summary' in your vault covers it."},
        {
            "role": "user",
            "content": (
                f"Make a project channel '{name}', and rename event {ev_id} to '{name} kickoff'."
            ),
        },
        _call_msg(f"call_{idx}_2", "create_channel", {"name": name, "kind": "project"}),
        _tool_msg(
            f"call_{idx}_2",
            "create_channel",
            {"id": f"ch_{idx}", "name": name, "kind": "project", "area": "프로젝트"},
        ),
        _call_msg(f"call_{idx}_3", "update_event", {"event_id": ev_id, "title": f"{name} kickoff"}),
        _tool_msg(
            f"call_{idx}_3",
            "update_event",
            {
                "id": ev_id,
                "title": f"{name} kickoff",
                "channel": "졸업프로젝트",
                "all_day": False,
                "starts_at": f"{DATES[3]}T10:00:00+09:00",
                "ends_at": f"{DATES[3]}T11:00:00+09:00",
            },
        ),
        {
            "role": "assistant",
            "content": f"Created #{name} and renamed the event to '{name} kickoff'.",
        },
    ]
    return {"scenario": "english_notes_and_channel", "messages": messages}


SCENARIOS: tuple[Callable[[random.Random, int], dict[str, Any]], ...] = (
    _scenario_add_and_update_task,
    _scenario_create_event_and_schedule,
    _scenario_search_knowledge_and_read_file,
    _scenario_workspace_edit_and_run_command,
    _scenario_lms_and_today_review,
    _scenario_grep_and_write_summary,
    _scenario_explore_workspace,
    _scenario_recover_from_error,
    _scenario_delete_with_approval,
    _scenario_no_tool_needed,
    _scenario_capture_and_inbox,
    _scenario_english_notes_and_channel,
)


def generate_examples(
    count: int = 60,
    eval_ratio: float = 0.15,
    seed: int = 42,
    holdout: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """`holdout`: scenario names that go to eval only, so eval measures generalisation
    to an unseen task shape instead of recall of a template."""
    rng = random.Random(seed)
    tools = tool_specs()
    examples: list[dict[str, Any]] = []
    for i in range(count):
        builder = SCENARIOS[i % len(SCENARIOS)]
        built = builder(rng, i + 1)
        run_id = f"seed-run-{hashlib.sha1(f'{seed}:{i}'.encode()).hexdigest()[:12]}"
        split = "eval" if built["scenario"] in holdout else split_of(run_id, eval_ratio)
        tool_steps = sum(1 for m in built["messages"] if m["role"] == "tool")
        examples.append(
            {
                "messages": built["messages"],
                "tools": tools,
                "metadata": {
                    "run_id": run_id,
                    "scenario": built["scenario"],
                    "tool_steps": tool_steps,
                    "split": split,
                    "synthetic": True,
                },
            }
        )
    return examples


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python training/generate_seed_data.py",
        description="Generate synthetic multi-turn Argos tool-calling SFT & eval JSONL data.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "training" / "data",
        help="Directory to write train.jsonl and eval.jsonl (default: training/data)",
    )
    parser.add_argument("--count", type=int, default=60, help="Total trajectories to synthesize")
    parser.add_argument("--eval-ratio", type=float, default=0.2, help="Fraction assigned to eval")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument(
        "--holdout-scenario",
        action="append",
        default=[],
        choices=[f.__name__.removeprefix("_scenario_") for f in SCENARIOS],
        help="put this scenario only in eval (repeatable): tests unseen task shapes",
    )
    args = parser.parse_args(argv)

    examples = generate_examples(
        count=args.count,
        eval_ratio=args.eval_ratio,
        seed=args.seed,
        holdout=frozenset(args.holdout_scenario),
    )
    args.out_dir.mkdir(parents=True, exist_ok=True)

    counts: dict[str, int] = {}
    for split in ("train", "eval"):
        chosen = [e for e in examples if e["metadata"]["split"] == split]
        target = args.out_dir / f"{split}.jsonl"
        target.write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in chosen),
            encoding="utf-8",
        )
        counts[split] = len(chosen)

    print(json.dumps({"out_dir": str(args.out_dir), **counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
