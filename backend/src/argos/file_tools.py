"""Workspace file and shell tools for the native harness (v0.2 Stage 1.2).

Two fences, on purpose separate:
- `harness.decide()` (before the call): reads and, in auto_edit, writes inside the run's
  workspace go ahead; shell commands, deletions and anything outside the workspace wait
  for the user's approval.
- `_resolve()` (inside the call): whatever was approved, a path must stay inside the
  run's allowed roots (its workspace, the Argos workspace, the channel's project folder
  and the job roots), with symlinks and `..` resolved, so a link cannot lead out.

Files a write or edit replaces are copied to `backup_dir` first."""

import asyncio
import contextlib
import fnmatch
import os
import re
import shutil
import signal
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from argos.harness import ToolContext, ToolDef, ToolRegistry
from argos.knowledge import extract_text

MAX_READ_LINES = 2000
MAX_READ_BYTES = 2_000_000
MAX_LISTED = 500
MAX_GREP_FILE = 2_000_000
MAX_COMMAND_OUTPUT = 30_000
DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 600
SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__", ".DS_Store"}


class OutsideRoots(PermissionError):
    pass


def _resolve(ctx: ToolContext, raw: str | None) -> Path:
    """A tool's path argument (relative: from the workspace) as a real path inside the
    allowed roots; raises OutsideRoots otherwise."""
    value = (raw or ".").strip() or "."
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = ctx.workspace / path
    real = path.resolve()
    for root in ctx.allowed_roots():
        if real.is_relative_to(root.expanduser().resolve()):
            return real
    raise OutsideRoots(f"허용된 작업 폴더 밖이에요: {value}")


def _shown(ctx: ToolContext, path: Path) -> str:
    """Paths in results: relative to the workspace when inside it."""
    workspace = ctx.workspace.resolve()
    return path.relative_to(workspace).as_posix() if path.is_relative_to(workspace) else str(path)


def _path_arg(key: str = "path") -> Any:
    def paths(args: dict[str, Any]) -> list[str]:
        return [str(args.get(key) or ".")]

    return paths


def _backup(ctx: ToolContext, path: Path) -> Path | None:
    """Copies `path` before it changes; returns the copy (None: nothing to keep)."""
    if ctx.backup_dir is None or not path.is_file():
        return None
    folder = ctx.backup_dir / (ctx.run_id or "manual")
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    copy = folder / f"{stamp}-{path.name}"
    shutil.copy2(path, copy)
    return copy


def _read_text(path: Path) -> str:
    if path.stat().st_size > MAX_READ_BYTES and path.suffix.lower() != ".pdf":
        raise ValueError(f"파일이 너무 커요 ({path.stat().st_size} bytes)")
    mime, body = extract_text(path, path.name)
    if not body and mime != "text/plain":
        raise ValueError(f"글자로 읽을 수 없는 파일이에요 ({mime})")
    return body


# --- reads ------------------------------------------------------------------------------


async def read_file(args: dict[str, Any], ctx: ToolContext) -> str:
    path = _resolve(ctx, args.get("path"))
    if not path.is_file():
        raise FileNotFoundError(f"파일이 없어요: {_shown(ctx, path)}")
    body = await asyncio.to_thread(_read_text, path)
    lines = body.splitlines()
    offset = max(1, int(args.get("offset") or 1))
    limit = max(1, min(int(args.get("limit") or MAX_READ_LINES), MAX_READ_LINES))
    chosen = lines[offset - 1 : offset - 1 + limit]
    numbered = "\n".join(f"{n:>6}\t{line}" for n, line in enumerate(chosen, offset))
    rest = len(lines) - (offset - 1 + len(chosen))
    tail = f"\n…({rest}줄 더 있음: offset={offset + len(chosen)})" if rest > 0 else ""
    return numbered + tail if numbered else "(빈 파일)"


def _list(path: Path) -> list[str]:
    entries: list[str] = []
    for child in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if len(entries) >= MAX_LISTED:
            entries.append(f"…(더 있음, {MAX_LISTED}개까지만 보여요)")
            break
        if child.is_symlink():
            entries.append(f"{child.name} -> (link)")
        elif child.is_dir():
            entries.append(f"{child.name}/")
        else:
            entries.append(f"{child.name}\t{child.stat().st_size}")
    return entries


async def list_dir(args: dict[str, Any], ctx: ToolContext) -> str:
    path = _resolve(ctx, args.get("path"))
    if not path.is_dir():
        raise NotADirectoryError(f"폴더가 아니에요: {_shown(ctx, path)}")
    entries = await asyncio.to_thread(_list, path)
    return "\n".join(entries) or "(빈 폴더)"


def _walk(base: Path) -> list[Path]:
    """Files under `base`, skipping heavy/hidden tool folders and symlinked folders."""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        found += [Path(dirpath) / f for f in sorted(filenames)]
    return found


async def glob_files(args: dict[str, Any], ctx: ToolContext) -> str:
    base = _resolve(ctx, args.get("path"))
    pattern = str(args.get("pattern") or "*")

    def run() -> list[str]:
        hits = [
            p
            for p in _walk(base)
            if fnmatch.fnmatch(p.relative_to(base).as_posix(), pattern)
            or fnmatch.fnmatch(p.name, pattern)
        ]
        return [_shown(ctx, p) for p in hits[:MAX_LISTED]]

    hits = await asyncio.to_thread(run)
    return "\n".join(hits) or "(찾은 파일 없음)"


async def grep_files(args: dict[str, Any], ctx: ToolContext) -> str:
    base = _resolve(ctx, args.get("path"))
    flags = re.IGNORECASE if args.get("ignore_case") else 0
    try:
        regex = re.compile(str(args.get("pattern") or ""), flags)
    except re.error as exc:
        raise ValueError(f"정규식 오류: {exc}") from None
    only = str(args.get("glob") or "*")
    most = max(1, min(int(args.get("max_results") or 100), 500))

    def run() -> list[str]:
        lines: list[str] = []
        files = [base] if base.is_file() else _walk(base)
        for path in files:
            if not fnmatch.fnmatch(path.name, only) or path.stat().st_size > MAX_GREP_FILE:
                continue
            try:
                body = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for n, line in enumerate(body.splitlines(), 1):
                if regex.search(line):
                    lines.append(f"{_shown(ctx, path)}:{n}: {line[:300]}")
                    if len(lines) >= most:
                        return lines
        return lines

    hits = await asyncio.to_thread(run)
    return "\n".join(hits) or "(일치하는 줄 없음)"


# --- writes -----------------------------------------------------------------------------


async def write_file(args: dict[str, Any], ctx: ToolContext) -> str:
    path = _resolve(ctx, args.get("path"))
    if path.is_dir():
        raise IsADirectoryError(f"폴더예요: {_shown(ctx, path)}")
    content = str(args.get("content") or "")

    def run() -> Path | None:
        copy = _backup(ctx, path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return copy

    copy = await asyncio.to_thread(run)
    kept = " (이전 내용은 백업해 뒀어요)" if copy else ""
    return f"{_shown(ctx, path)}에 {len(content.encode())} bytes를 썼어요{kept}"


async def edit_file(args: dict[str, Any], ctx: ToolContext) -> str:
    """Replaces `old_string` with `new_string` after checking the file still holds it
    (exactly once, unless replace_all), backing the original up first."""
    path = _resolve(ctx, args.get("path"))
    old, new = str(args.get("old_string") or ""), str(args.get("new_string") or "")
    if not old:
        raise ValueError("old_string이 비어 있어요 (새 파일은 write_file로 쓰세요)")
    if old == new:
        raise ValueError("old_string과 new_string이 같아요")
    if not path.is_file():
        raise FileNotFoundError(f"파일이 없어요: {_shown(ctx, path)}")

    def run() -> int:
        body = path.read_text(encoding="utf-8")
        count = body.count(old)
        if count == 0:
            raise ValueError("파일에 old_string이 없어요. read_file로 지금 내용을 다시 확인하세요")
        if count > 1 and not args.get("replace_all"):
            raise ValueError(
                f"old_string이 {count}곳에 있어요. 앞뒤 줄을 더 넣어 한 곳만 맞게 하거나 "
                "replace_all=true로 부르세요"
            )
        _backup(ctx, path)
        path.write_text(
            body.replace(old, new) if args.get("replace_all") else body.replace(old, new, 1),
            encoding="utf-8",
        )
        return count if args.get("replace_all") else 1

    replaced = await asyncio.to_thread(run)
    return f"{_shown(ctx, path)}: {replaced}곳을 바꿨어요 (원본은 백업해 뒀어요)"


async def delete_file(args: dict[str, Any], ctx: ToolContext) -> str:
    path = _resolve(ctx, args.get("path"))
    if any(path == root.expanduser().resolve() for root in ctx.allowed_roots()):
        raise PermissionError("작업 폴더 자체는 지울 수 없어요")
    if not path.exists():
        raise FileNotFoundError(f"없어요: {_shown(ctx, path)}")

    def run() -> None:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            _backup(ctx, path)
            path.unlink()

    await asyncio.to_thread(run)
    return f"{_shown(ctx, path)}을(를) 지웠어요"


async def run_command(args: dict[str, Any], ctx: ToolContext) -> str:
    """A shell command in a folder inside the roots, stopped (with its children) at the
    timeout; output is stdout and stderr together, cut at MAX_COMMAND_OUTPUT."""
    command = str(args.get("command") or "").strip()
    if not command:
        raise ValueError("command가 비어 있어요")
    cwd = _resolve(ctx, args.get("cwd"))
    if not cwd.is_dir():
        raise NotADirectoryError(f"폴더가 아니에요: {_shown(ctx, cwd)}")
    timeout = max(1, min(int(args.get("timeout") or DEFAULT_TIMEOUT), MAX_TIMEOUT))
    process = await asyncio.create_subprocess_shell(
        command,
        cwd=cwd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,  # its own process group: the timeout stops children too
    )
    assert process.stdout is not None
    chunks: list[bytes] = []
    size = 0
    timed_out = False

    async def collect() -> None:
        nonlocal size
        assert process.stdout is not None
        while chunk := await process.stdout.read(65536):
            if size < MAX_COMMAND_OUTPUT:
                chunks.append(chunk[: MAX_COMMAND_OUTPUT - size])
            size += len(chunk)
        await process.wait()

    try:
        await asyncio.wait_for(collect(), timeout)
    except TimeoutError:
        timed_out = True
    finally:
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            await process.wait()
    output = b"".join(chunks).decode("utf-8", errors="replace")
    if size > MAX_COMMAND_OUTPUT:
        output += f"\n…(출력 {size - MAX_COMMAND_OUTPUT} bytes 생략)"
    head = f"{timeout}초가 지나 멈췄어요" if timed_out else f"exit code {process.returncode}"
    return f"{head}\n{output}".rstrip()


# --- registration -----------------------------------------------------------------------

_STR = {"type": "string"}


def _schema(required: list[str], **props: Any) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required}


FILE_TOOLS = [
    ToolDef(
        "read_file",
        "Read a text file (or a PDF's text) in the workspace, with line numbers. "
        "Paths are relative to the workspace. offset/limit page through long files.",
        _schema(
            ["path"],
            path=_STR,
            offset={"type": "integer", "minimum": 1},
            limit={"type": "integer", "minimum": 1, "maximum": MAX_READ_LINES},
        ),
        "read",
        read_file,
        paths=_path_arg(),
    ),
    ToolDef(
        "list_dir",
        "List a folder in the workspace (folders end with /, files show their size).",
        _schema([], path=_STR),
        "read",
        list_dir,
        paths=_path_arg(),
    ),
    ToolDef(
        "glob_files",
        "Find files by glob pattern (e.g. **/*.py or *.md) under a folder.",
        _schema(["pattern"], pattern=_STR, path=_STR),
        "read",
        glob_files,
        paths=_path_arg(),
    ),
    ToolDef(
        "grep_files",
        "Search file contents with a regular expression; returns path:line: text.",
        _schema(
            ["pattern"],
            pattern=_STR,
            path=_STR,
            glob={"type": "string", "description": "only files whose name matches, e.g. *.py"},
            ignore_case={"type": "boolean"},
            max_results={"type": "integer", "minimum": 1, "maximum": 500},
        ),
        "read",
        grep_files,
        paths=_path_arg(),
    ),
    ToolDef(
        "write_file",
        "Create or overwrite a text file in the workspace (the old content is backed up).",
        _schema(["path", "content"], path=_STR, content=_STR),
        "write",
        write_file,
        paths=_path_arg(),
        summarize=lambda a: f"파일 쓰기: {a.get('path')}",
    ),
    ToolDef(
        "edit_file",
        "Replace old_string with new_string in a file. old_string must match the current "
        "text exactly once (or use replace_all). Read the file first.",
        _schema(
            ["path", "old_string", "new_string"],
            path=_STR,
            old_string=_STR,
            new_string=_STR,
            replace_all={"type": "boolean"},
        ),
        "write",
        edit_file,
        paths=_path_arg(),
        summarize=lambda a: f"파일 수정: {a.get('path')}",
    ),
    ToolDef(
        "delete_file",
        "Delete a file or folder in the workspace. The user approves it first.",
        _schema(["path"], path=_STR),
        "destructive",
        delete_file,
        paths=_path_arg(),
        summarize=lambda a: f"파일 삭제: {a.get('path')}",
    ),
    ToolDef(
        "run_command",
        "Run a shell command (zsh/sh) in a workspace folder. The user approves it first. "
        f"Stops after `timeout` seconds (default {DEFAULT_TIMEOUT}, max {MAX_TIMEOUT}).",
        _schema(
            ["command"],
            command=_STR,
            cwd={"type": "string", "description": "folder to run in (default: workspace)"},
            timeout={"type": "integer", "minimum": 1, "maximum": MAX_TIMEOUT},
        ),
        "destructive",
        run_command,
        paths=_path_arg("cwd"),
        summarize=lambda a: f"명령 실행: {str(a.get('command'))[:200]}",
    ),
]


def register_file_tools(registry: ToolRegistry) -> None:
    for tool in FILE_TOOLS:
        registry.register(tool, replace=True)
