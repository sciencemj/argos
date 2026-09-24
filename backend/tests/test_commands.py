from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from argos.commands import (
    AskCommand,
    CommandError,
    EventCommand,
    NoteCommand,
    TaskCommand,
    find_date,
    find_time,
    parse,
)

SEOUL = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=SEOUL)  # Thursday


def kst(*args: int) -> datetime:
    return datetime(*args, tzinfo=SEOUL)


def test_plain_text_is_not_a_command() -> None:
    assert parse("금요일까지 과제2", NOW, SEOUL) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/task 과제2 제출", TaskCommand("과제2 제출", None)),
        ("/task 과제2 제출 금", TaskCommand("과제2 제출", kst(2026, 9, 25, 23, 59))),
        ("/task 과제2 제출 금요일까지", TaskCommand("과제2 제출", kst(2026, 9, 25, 23, 59))),
        ("/task 과제2 목요일", TaskCommand("과제2", kst(2026, 9, 24, 23, 59))),
        ("/task 과제2 수", TaskCommand("과제2", kst(2026, 9, 30, 23, 59))),
        ("/task 발표 다음주 월 10:00", TaskCommand("발표", kst(2026, 9, 28, 10, 0))),
        ("/task 발표자료 다음주 목", TaskCommand("발표자료", kst(2026, 10, 1, 23, 59))),
        ("/task 보고서 내일 18시", TaskCommand("보고서", kst(2026, 9, 25, 18, 0))),
        ("/task 보고서 9/26 23:00", TaskCommand("보고서", kst(2026, 9, 26, 23, 0))),
        ("/task 보고서 2026-10-02", TaskCommand("보고서", kst(2026, 10, 2, 23, 59))),
        ("/task 오늘 할 일 정리 15:00", TaskCommand("오늘 할 일 정리", kst(2026, 9, 24, 15, 0))),
    ],
)
def test_task(text: str, expected: TaskCommand) -> None:
    assert parse(text, NOW, SEOUL) == expected


def test_month_day_in_the_past_rolls_to_next_year() -> None:
    december = datetime(2026, 12, 20, tzinfo=SEOUL)
    command = parse("/task 계획 1/5", december, SEOUL)
    assert command == TaskCommand("계획", kst(2027, 1, 5, 23, 59))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "/event 3주차 퀴즈 9/30 10:30-11:15",
            EventCommand("3주차 퀴즈", kst(2026, 9, 30, 10, 30), kst(2026, 9, 30, 11, 15)),
        ),
        (
            "/event 개강총회 금 19:00",
            EventCommand("개강총회", kst(2026, 9, 25, 19), kst(2026, 9, 25, 20)),
        ),
        ("/event 중간고사 2026-10-20", EventCommand("중간고사", all_day=date(2026, 10, 20))),
    ],
)
def test_event(text: str, expected: EventCommand) -> None:
    assert parse(text, NOW, SEOUL) == expected


def test_note_and_ask() -> None:
    assert parse("/note LRU vs Clock 정리", NOW, SEOUL) == NoteCommand("LRU vs Clock 정리")
    assert parse("/ask forwarding이 뭐야", NOW, SEOUL) == AskCommand("forwarding이 뭐야")


@pytest.mark.parametrize(
    "text",
    [
        "/todo 뭔가",
        "/task",
        "/task 금",
        "/event 회의",
        "/event 회의 9/31",
        "/event 회의 금 15:00-14:00",
        "/task 회의 금 25:00",
        "/note",
    ],
)
def test_errors(text: str) -> None:
    with pytest.raises(CommandError):
        parse(text, NOW, SEOUL)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("금요일까지 과제2", date(2026, 9, 25)),
        ("KUBIG 발표자료 다음주 수요일까지", date(2026, 9, 30)),
        ("다음주 화요일 오후 3시 조교 면담", date(2026, 9, 29)),
        ("내일 회의", date(2026, 9, 25)),
        ("10/2에 발표", date(2026, 10, 2)),
        ("ViT 파인튜닝 비교해보기", None),
    ],
)
def test_find_date(text: str, expected: date | None) -> None:
    assert find_date(text, NOW.date()) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("오후 3시 면담", time(15, 0)),
        ("오전 10시 반 수업", time(10, 30)),
        ("3시 20분", time(3, 20)),
        ("15:40 세미나", time(15, 40)),
        ("시간 미정", None),
    ],
)
def test_find_time(text: str, expected: time | None) -> None:
    assert find_time(text) == expected
