import pytest

from argos.course_names import CourseName, clean_course_name


@pytest.mark.parametrize(
    ("raw", "title", "section"),
    [
        ("262R (서울-학부)운영체제(OPERATING SYSTEMS)-02분반", "운영체제", "02분반"),
        (
            "262R (서울-학부)컴퓨터구조(영강)(COMPUTER ARCHITECTURE(English))-02분반",
            "컴퓨터구조",
            "02분반",
        ),
        (
            "262R (서울-학부)비즈니스애널리틱스II(영강)(BUSINESS ANALYTICS II(English))-00분반",
            "비즈니스애널리틱스II",
            "00분반",
        ),
        ("[학생] [한국어] 2026 인권과 성평등 교육 - 04분반", "인권과 성평등 교육", "04분반"),
        ("[2026-1] 자료구조 (01)", "자료구조", "01"),
        ("2026 Spring Linear Algebra", "Linear Algebra", None),
        ("2026-2 Fall Data Structures - Section 02", "Data Structures", "Section 02"),
        ("Data Structures Sec. 3", "Data Structures", "Sec. 3"),
        ("CS101 Intro to CS", "CS101 Intro to CS", None),
        ("3D Modeling", "3D Modeling", None),
        ("Calculus 2", "Calculus 2", None),
        ("알림·메시지", "알림·메시지", None),
    ],
)
def test_clean_course_name(raw: str, title: str, section: str | None) -> None:
    assert clean_course_name(raw) == CourseName(title, section)


@pytest.mark.parametrize("raw", ["(영강)", "[공지]", "2026 (A)", "262R X"])
def test_keeps_original_when_nothing_meaningful_is_left(raw: str) -> None:
    assert clean_course_name(raw) == CourseName(raw, None)
