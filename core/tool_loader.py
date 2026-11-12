import importlib
import inspect
import pkgutil

from threading import RLock
from typing    import Any

from core.tool_manager import ToolManager

import tools


class ToolError(Exception):
    """Raised when capability loading fails"""
    ...


class ToolLoader:
    def __init__(
        self,
        tool_manager: ToolManager,
        dependencies: dict[str, Any] | None = None
    ):
        self.tool_manager = tool_manager
        self._dependencies = dependencies or {}
        self._loaded = set()
        self._failed = {}
        self._lock = RLock()

    def discover(self) -> list[str]:
        discovered = []

        for module in pkgutil.iter_modules(tools.__path__):

            # Only discover packages, not individual Python files
            if not module.ispkg:
                continue

            if module.name.startswith("_"):
                continue

            discovered.append(module.name)

        return sorted(discovered)


    def load(self, name: str) -> None:
        with self._lock:
            if name in self._loaded:
                return

            if name.startswith("_") or not name.isidentifier():
                raise ToolError(f"Invalid tool name: {name}")

            available = self.discover()

            if name not in available:
                raise ToolError(f"Tool not found: {name}")

            module_name = f"tools.{name}"

            # Record existing tools for rollback
            previous_tools = set(self.tool_manager.list_tools())

            try:
                module = importlib.import_module(module_name)

                register = getattr(module, "register", None)

                if not callable(register):
                    raise ToolError(f"Tool '{name}' has no register() function.")

                params = inspect.signature(register).parameters
                kwargs = {
                    key: value
                    for key, value in self._dependencies.items()
                    if key in params
                }
                register(self.tool_manager, **kwargs)

                self._loaded.add(name)
                self._failed.pop(name, None)

            except Exception as exc:
                
                # Remove tools registered by a failed capability.
                current_tools = set(self.tool_manager.list_tools())

                new_tools = current_tools - previous_tools

                for tool_name in new_tools:
                    self.tool_manager.unregister(tool_name)

                self._failed[name] = str(exc)

                raise ToolError(f"Failed to load '{name}': {exc}") from exc

    def load_all(self) -> dict:
        results = {
            "loaded": [],
            "failed": {}
        }

        for name in self.discover():
            try:
                self.load(name)
                results["loaded"].append(name)
            except ToolError as exc:
                results["failed"][name] = str(exc)

        return results

    def loaded(self) -> list[str]:
        with self._lock:
            return sorted(self._loaded)

    def failed(self) -> dict:
        with self._lock:
            return self._failed.copy()