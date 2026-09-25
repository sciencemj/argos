"""Obsidian vault (PLAN Phase 8) against a copy of tests/fixtures/vault — never a real
vault. File edits are checked byte for byte: only the intended lines may change."""

import shutil
from collections.abc import Iterator
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from argos import vault
from argos.config import Settings
from argos.main import create_app

FIXTURE = Path(__file__).parent / "fixtures" / "vault"
INTRO = "Courses/Algorithms/01 Intro.md"
HOMEWORK = "Courses/Algorithms/Homework.md"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    copy = tmp_path / "My Vault"
    shutil.copytree(FIXTURE, copy)
    today = datetime.now(UTC).date().isoformat()
    (copy / "Daily" / f"{today}.md").write_text(
        f"# {today}\n\n- [ ] 장보기\n- [x] 운동\n"
        "- [ ] #task 치과 예약 14:30~15:00 (시청역) https://example.com/dentist\n"
    )
    return copy


@pytest.fixture
def app(settings: Settings, root: Path, tmp_path: Path) -> Iterator[TestClient]:
    config = settings.model_copy(update={"vault_backup_dir": tmp_path / "backups"})
    with TestClient(create_app(config)) as client:
        client.app.state.vault_sync.debounce = 60  # type: ignore[attr-defined]  # tests sync by hand
        body = client.put("/api/v1/settings/vault", json={"path": str(root)})
        assert body.status_code == 200, body.text
        yield client


def snapshot(root: Path) -> dict[str, bytes]:
    return {r: p.read_bytes() for r, p in vault.walk(root)}


def changed_lines(before: bytes, after: bytes) -> list[tuple[str, str]]:
    old, new = before.decode().split("\n"), after.decode().split("\n")
    assert len(old) == len(new), "a line was added or removed"
    return [(a, b) for a, b in zip(old, new, strict=True) if a != b]


def channel_named(client: TestClient, name: str) -> dict[str, Any]:
    return next(c for c in client.get("/api/v1/channels").json()["channels"] if c["name"] == name)


def link_folder(client: TestClient, channel: str, folder: str) -> dict[str, Any]:
    response = client.patch(
        f"/api/v1/channels/{channel_named(client, channel)['id']}", json={"vault_path": folder}
    )
    assert response.status_code == 200, response.text
    return response.json()


def sync(client: TestClient) -> dict[str, Any]:
    status = client.post("/api/v1/vault/sync").json()["status"]
    assert status["last_error"] is None, status["last_error"]
    return status


def tasks_of(client: TestClient, channel: str) -> dict[str, dict[str, Any]]:
    channel_id = channel_named(client, channel)["id"]
    return {
        t["title"]: t for t in client.get("/api/v1/tasks", params={"channel_id": channel_id}).json()
    }


# --- parsing ---------------------------------------------------------------------------------


def test_parse_tasks_reads_common_checkbox_styles() -> None:
    body = (FIXTURE / INTRO).read_text()
    tasks = {t.title: t for t in vault.parse_tasks(body)}
    assert set(tasks) == {
        "교재 1장 읽기",
        "연습문제 풀기",
        "강의 자료 내려받기",
        "취소된 일",
        "이미 ID가 있는 일",
        "별표 목록 할 일",
    }  # nothing from the frontmatter or the code block
    assert (tasks["교재 1장 읽기"].due, tasks["교재 1장 읽기"].priority) == (date(2026, 10, 2), 2)
    assert tasks["연습문제 풀기"].due == date(2026, 10, 5)  # Dataview style
    assert tasks["강의 자료 내려받기"].done
    assert tasks["취소된 일"].mark == "-"
    assert tasks["이미 ID가 있는 일"].block == "mine-1"


def test_titles_leave_out_tags_times_and_links() -> None:
    [task] = vault.parse_tasks(
        "- [ ] #task [AI챌린지] 코드 제출 마감 (주제3) 14:00 https://example.com/t #ai"
        " 📅 2026-10-06"
    )
    assert task.title == "[AI챌린지] 코드 제출 마감 (주제3)"
    assert (task.due, task.at) == (date(2026, 10, 6), time(14, 0))
    assert task.links == ["https://example.com/t"]
    [plain] = vault.parse_tasks("- [ ] 회의 10:00~11:30 #work")
    assert (plain.title, plain.at) == ("회의", time(10, 0))
    [kept] = vault.parse_tasks("- [ ] 16:9 슬라이드 #1 이슈, C# 예제")
    assert kept.title == "16:9 슬라이드 #1 이슈, C# 예제"  # no time, no tags (Obsidian
    assert kept.at is None  # tags cannot be all digits)


def test_line_edits_keep_everything_else() -> None:
    line = "  - [ ] 과제 1 제출 📅 2026-10-10\r"
    assert vault.with_mark(line, done=True) == "  - [x] 과제 1 제출 📅 2026-10-10\r"
    assert (
        vault.with_block(line, "argos-abc123")
        == "  - [ ] 과제 1 제출 📅 2026-10-10 ^argos-abc123\r"
    )
    assert vault.with_mark("- [x] a ^argos-1", done=False) == "- [ ] a ^argos-1"


def test_daily_folder_comes_from_the_vault_settings(root: Path) -> None:
    assert vault.detect_daily_folder(root) == "Daily"
    assert vault.detect_daily_folder(root / "Notes") is None


def test_known_vaults_come_from_obsidian(
    monkeypatch: pytest.MonkeyPatch, root: Path, tmp_path: Path
) -> None:
    config = tmp_path / "obsidian"
    config.mkdir()
    (config / "obsidian.json").write_text(
        f'{{"vaults": {{"a": {{"path": "{root}"}}, "b": {{"path": "{tmp_path}/gone"}}}}}}'
    )
    monkeypatch.setattr(vault, "obsidian_config_dir", lambda: config)
    assert vault.detect_vaults() == [root]


def test_paths_cannot_leave_the_vault(root: Path, tmp_path: Path) -> None:
    (root / "escape").symlink_to(tmp_path)
    for bad in ("../outside.md", "/etc/passwd", "escape/x"):
        with pytest.raises(vault.services.InvalidError):
            vault.resolve_inside(root, bad)


# --- index and search -------------------------------------------------------------------------


def test_index_and_search(app: TestClient, root: Path) -> None:
    status = app.get("/api/v1/settings/vault").json()
    assert status["daily_folder"] == "Daily"  # detected
    assert status["status"]["notes"] == 7  # .obsidian and .trash are skipped
    assert any("그래프 이론 2.md" in w for w in status["status"]["warnings"])  # sync conflict copy

    hits = app.get("/api/v1/notes", params={"q": "알고리즘"}).json()
    assert {h["title"] for h in hits} == {"알고리즘 1주차", "그래프 이론"}  # inside "알고리즘의"
    assert all("[알고리즘]" in h["snippet"] for h in hits)
    assert [h["title"] for h in app.get("/api/v1/notes", params={"q": "과제"}).json()] == [
        "Homework"
    ]

    (root / "Notes" / "그래프 이론.md").write_text("# 그래프 이론\n\n다익스트라 최단 경로\n")
    (root / "Courses" / "Databases" / "Normalization.md").unlink()
    sync(app)
    assert [h["title"] for h in app.get("/api/v1/notes", params={"q": "다익스트라"}).json()] == [
        "그래프 이론"
    ]
    assert app.get("/api/v1/notes", params={"q": "알고리즘의 기초"}).json() == []
    assert app.get("/api/v1/settings/vault").json()["status"]["notes"] == 6


def test_channel_notes_materials_and_reading(app: TestClient, root: Path) -> None:
    channel = link_folder(app, "컴퓨터구조", "Courses/Algorithms/")
    assert channel["vault_path"] == "Courses/Algorithms"
    sync(app)
    notes = app.get("/api/v1/notes", params={"channel_id": channel["id"]}).json()
    assert {n["title"] for n in notes} == {"알고리즘 1주차", "Homework"}
    intro = next(n for n in notes if n["title"] == "알고리즘 1주차")
    assert intro["tags"] == ["algorithms", "week1"]
    body = app.get(f"/api/v1/notes/{intro['id']}").json()["body"]
    assert body.startswith("# 알고리즘 개요") and "tags:" not in body

    [pdf] = app.get(f"/api/v1/channels/{channel['id']}/materials").json()
    assert (pdf["name"], pdf["folder"], pdf["path"]) == (
        "week1.pdf",
        "Slides",
        "Courses/Algorithms/Slides/week1.pdf",
    )
    served = app.get("/api/v1/vault/file", params={"path": pdf["path"]})
    assert served.status_code == 200 and served.content.startswith(b"%PDF")
    assert app.get("/api/v1/vault/file", params={"path": "../secret"}).status_code == 422


def test_notes_sort_by_name_path_or_date(app: TestClient, root: Path) -> None:
    def titles(**params: str) -> list[str]:
        return [n["title"] for n in app.get("/api/v1/notes", params=params).json()]

    by_name = titles(sort="title")
    assert by_name == sorted(by_name, key=str.casefold)
    assert titles(sort="title", order="desc") == by_name[::-1]
    by_path = [n["vault_path"] for n in app.get("/api/v1/notes", params={"sort": "path"}).json()]
    assert by_path == sorted(by_path, key=str.casefold)
    (root / "Notes" / "그래프 이론.md").write_text("# 그래프 이론\n\n알고리즘 추가\n")
    sync(app)
    assert titles()[0] == "그래프 이론"  # newest first by default
    assert titles(order="asc")[-1] == "그래프 이론"
    matches = titles(q="알고리즘")
    assert titles(q="알고리즘", sort="title") == sorted(matches, key=str.casefold)


def test_channel_folder_must_exist(app: TestClient) -> None:
    missing = app.patch(
        f"/api/v1/channels/{channel_named(app, '컴퓨터구조')['id']}", json={"vault_path": "Nope"}
    )
    assert missing.status_code == 422
    assert "'Nope' 폴더가 없어요" in missing.json()["error"]["message"]


# --- tasks ------------------------------------------------------------------------------------


def test_tasks_come_in_with_one_line_changed_each(app: TestClient, root: Path) -> None:
    before = snapshot(root)
    link_folder(app, "컴퓨터구조", "Courses/Algorithms")
    status = sync(app)
    assert status["last_tasks"]["imported"] == 6  # (the daily note's came in on connect)

    tasks = tasks_of(app, "컴퓨터구조")
    assert set(tasks) == {
        "교재 1장 읽기",
        "연습문제 풀기",
        "이미 ID가 있는 일",
        "별표 목록 할 일",
        "과제 1 제출",
        "과제 2 조사",
    }
    assert tasks["교재 1장 읽기"]["due_at"] == "2026-10-02T14:59:00Z"  # 23:59 that day, KST
    assert tasks["교재 1장 읽기"]["priority"] == 2
    assert tasks["교재 1장 읽기"]["description"] == "노트: 알고리즘 1주차"

    after = snapshot(root)
    changed = {r for r in before if before[r] != after.get(r)}
    assert changed == {INTRO, HOMEWORK}
    intro_edits = changed_lines(before[INTRO], after[INTRO])
    assert len(intro_edits) == 3  # the task with its own ^mine-1 needed no ID
    for old, new in intro_edits:
        assert new.startswith(old.rstrip()) and new.removeprefix(old.rstrip()).startswith(
            " ^argos-"
        )
    homework_edits = changed_lines(before[HOMEWORK], after[HOMEWORK])
    assert all(new.endswith("\r") for _, new in homework_edits)  # CRLF kept

    assert sync(app)["last_tasks"]["imported"] == 0  # idempotent: nothing written again
    assert snapshot(root) == after


def test_recent_daily_notes_go_to_the_personal_channel(app: TestClient, root: Path) -> None:
    personal = channel_named(app, "일상")
    assert personal["vault_path"] == "Daily"  # linked when the vault was connected
    notes = app.get("/api/v1/notes", params={"channel_id": personal["id"]}).json()
    assert len(notes) == 2  # both daily notes are readable in #일상
    sync(app)
    tasks = tasks_of(app, "일상")
    assert set(tasks) == {"장보기", "치과 예약 (시청역)"}  # not the 2020 note, not 운동 (done)
    day = datetime.now(UTC).date()  # the daily note's name; its tasks are due that day
    assert tasks["장보기"]["due_at"] == f"{day}T14:59:00Z"  # 23:59 KST
    dentist = tasks["치과 예약 (시청역)"]  # the old 2020 note stays out despite the link
    assert dentist["due_at"] == f"{day}T05:30:00Z"  # 14:30 KST, from the line
    assert dentist["description"].endswith("https://example.com/dentist")


def test_checking_in_argos_ticks_only_that_box(app: TestClient, root: Path, tmp_path: Path) -> None:
    link_folder(app, "컴퓨터구조", "Courses/Algorithms")
    sync(app)
    before = (root / HOMEWORK).read_bytes()
    task = tasks_of(app, "컴퓨터구조")["과제 2 조사"]
    app.patch(f"/api/v1/tasks/{task['id']}", json={"status": "done"})
    assert sync(app)["last_tasks"]["checked_in_argos"] == 1
    [(old, new)] = changed_lines(before, (root / HOMEWORK).read_bytes())
    assert old.startswith("- [ ] 과제 2 조사") and new == old.replace("- [ ]", "- [x]", 1)
    backups = sorted((tmp_path / "backups").rglob("Homework.md"))
    assert backups and backups[-1].read_bytes() == before

    app.patch(f"/api/v1/tasks/{task['id']}", json={"status": "todo"})
    sync(app)
    assert (root / HOMEWORK).read_bytes() == before


def test_checking_in_the_note_completes_the_task(app: TestClient, root: Path) -> None:
    link_folder(app, "컴퓨터구조", "Courses/Algorithms")
    sync(app)
    path = root / INTRO
    text = path.read_text()
    text = text.replace("- [ ] 교재 1장 읽기", "- [x] 교재 1장 읽기").replace(
        "연습문제 풀기", "연습문제 3개 풀기"
    )
    path.write_text(text)
    status = sync(app)["last_tasks"]
    assert (status["checked_in_note"], status["updated"]) == (1, 1)
    tasks = tasks_of(app, "컴퓨터구조")
    assert tasks["교재 1장 읽기"]["status"] == "done"
    assert "연습문제 3개 풀기" in tasks
    assert path.read_text() == text  # nothing written back


def test_removed_lines_and_deleted_tasks_are_let_go(app: TestClient, root: Path) -> None:
    link_folder(app, "컴퓨터구조", "Courses/Algorithms")
    sync(app)
    tasks = tasks_of(app, "컴퓨터구조")
    app.delete(f"/api/v1/tasks/{tasks['별표 목록 할 일']['id']}")
    path = root / INTRO
    path.write_text(
        "\n".join(line for line in path.read_text().split("\n") if "연습문제" not in line)
    )
    before = snapshot(root)
    status = sync(app)["last_tasks"]
    assert status["unlinked"] == 1 and status["imported"] == 0  # the deleted one is not re-imported
    assert "연습문제 풀기" in tasks_of(app, "컴퓨터구조")  # the task stays in Argos
    assert snapshot(root) == before


def test_a_file_changed_mid_edit_is_left_alone(root: Path, tmp_path: Path) -> None:
    original = (root / HOMEWORK).read_bytes()
    (root / HOMEWORK).write_bytes(original + b"- [ ] new line\n")
    with pytest.raises(vault.VaultChanged):
        vault.edit_lines(
            root, HOMEWORK, original, {2: lambda line: vault.with_mark(line, True)}, tmp_path
        )
    assert (root / HOMEWORK).read_bytes() == original + b"- [ ] new line\n"


# --- quick notes ------------------------------------------------------------------------------


def test_study_note_becomes_a_new_file_in_the_channel_folder(app: TestClient, root: Path) -> None:
    channel = link_folder(app, "컴퓨터구조", "Courses/Algorithms")
    before = snapshot(root)
    sent = app.post(
        f"/api/v1/channels/{channel['id']}/messages",
        json={"body": "/note 분할 정복은 문제를 나눠 푼다"},
    )
    item_id = sent.json()["ref_id"]
    accepted = app.post(f"/api/v1/inbox/{item_id}/accept", json={})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["object_type"] == "note_ref"

    after = snapshot(root)
    [new] = set(after) - set(before)
    assert new.startswith("Courses/Algorithms/") and new.endswith("분할 정복은 문제를 나눠 푼다.md")
    assert after[new].decode() == "# 분할 정복은 문제를 나눠 푼다\n\n분할 정복은 문제를 나눠 푼다\n"
    assert all(after[r] == before[r] for r in before)  # no existing file touched
    note = app.get(f"/api/v1/notes/{accepted.json()['id']}").json()
    assert note["channel_id"] == channel["id"]


def test_study_note_needs_a_channel_folder(app: TestClient) -> None:
    channel = channel_named(app, "운영체제")
    sent = app.post(f"/api/v1/channels/{channel['id']}/messages", json={"body": "/note 스케줄링"})
    refused = app.post(f"/api/v1/inbox/{sent.json()['ref_id']}/accept", json={})
    assert refused.status_code == 422
    assert "볼트 폴더를 먼저 지정" in refused.json()["error"]["message"]


def test_without_a_vault_nothing_happens(client: TestClient) -> None:
    status = client.get("/api/v1/settings/vault").json()
    assert (status["path"], status["status"]["notes"]) == (None, 0)
    assert client.get("/api/v1/notes").json() == []
