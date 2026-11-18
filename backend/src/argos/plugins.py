"""Tool plugins for the native harness (v0.2 Stage 1.2): `data_dir/plugins/<name>.py`
files that define `register(registry)` and call `registry.register(ToolDef(...))`.

Each tool is stamped `source="plugin:<name>"`, `plugin=<name>`, so the permission gate
treats it by its declared `kind` like any core tool. A plugin may not replace a core
tool, and a plugin that fails to load (import error, exception in `register`, a name
clash) adds nothing: its tools are rolled back and the error is reported.

Plugins are local Python code the user put there themselves; they run with the app's
permissions, so Argos only loads them from its own data folder."""

import importlib.util
import logging
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

from argos.harness import ToolDef, ToolRegistry

log = logging.getLogger(__name__)
PLUGIN_PREFIX = "plugin:"


@dataclass
class PluginStatus:
    name: str
    path: str
    tools: list[str] = field(default_factory=list[str])
    error: str | None = None


class PluginRegistry:
    """What a plugin's `register` gets: adds tools to the real registry under the
    plugin's name, never over a tool that is not its own."""

    def __init__(self, registry: ToolRegistry, name: str) -> None:
        self._registry = registry
        self.name = name
        self.added: list[str] = []

    def register(self, tool: ToolDef, *, replace_own: bool = True) -> ToolDef:
        existing = self._registry.get(tool.name)
        if existing is not None and existing.plugin != self.name:
            raise ValueError(f"tool {tool.name!r} already exists ({existing.source})")
        stamped = replace(tool, source=f"{PLUGIN_PREFIX}{self.name}", plugin=self.name)
        self._registry.register(stamped, replace=replace_own)
        if tool.name not in self.added:
            self.added.append(tool.name)
        return stamped


def unload_plugins(registry: ToolRegistry) -> None:
    for tool in registry.tools():
        if tool.source.startswith(PLUGIN_PREFIX):
            registry.unregister(tool.name)


def load_plugins(directory: Path, registry: ToolRegistry) -> list[PluginStatus]:
    """(Re)loads every `*.py` in `directory` (names starting with "_" are skipped)."""
    unload_plugins(registry)
    if not directory.is_dir():
        return []
    statuses: list[PluginStatus] = []
    for path in sorted(directory.glob("*.py")):
        name = path.stem
        if name.startswith("_"):
            continue
        status = PluginStatus(name=name, path=str(path))
        statuses.append(status)
        if not path.resolve().is_relative_to(directory.resolve()):
            # A link out of the plugins folder: only code put there is loaded.
            status.error = "plugins 폴더 밖을 가리키는 링크라 건너뛰었어요"
            log.warning("tool plugin %s points outside %s; skipped", name, directory)
            continue
        proxy = PluginRegistry(registry, name)
        module_name = f"argos_plugin_{name}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise ImportError("cannot load this file")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            register = getattr(module, "register", None)
            if not callable(register):
                raise AttributeError("no register(registry) function")
            register(proxy)
            status.tools = list(proxy.added)
        except Exception as exc:
            for tool_name in proxy.added:
                registry.unregister(tool_name)
            sys.modules.pop(module_name, None)
            status.error = f"{type(exc).__name__}: {exc}"
            log.warning("tool plugin %s failed to load: %s", name, status.error)
    return statuses
