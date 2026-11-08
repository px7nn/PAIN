from copy        import deepcopy
from dataclasses import dataclass
from threading   import RLock
from typing      import Callable


class ToolError(Exception):
    """Raised when a tool registration or execution fails"""
    ...


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Callable


class ToolManager:
    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._lock = RLock()

    # --------------------------------------------------
    # REGISTRATION
    # --------------------------------------------------

    def register(
        self,
        name: str,
        description: str,
        parameters: dict,
        handler: Callable
    ) -> None:

        if not name or not isinstance(name, str):
            raise ToolError("Invalid tool name.")

        if not description or not isinstance(description, str):
            raise ToolError("Invalid tool description.")

        if not isinstance(parameters, dict):
            raise ToolError("Parameters must be a dictionary.")

        if parameters.get("type") != "object":
            raise ToolError("Tool parameters must use an object schema.")

        if not callable(handler):
            raise ToolError("Handler must be callable.")

        with self._lock:
            if name in self._tools:
                raise ToolError(f"Tool already registered: {name}")

            self._tools[name] = Tool(
                name=name,
                description=description,
                parameters=deepcopy(parameters),
                handler=handler
            )

    # --------------------------------------------------
    # REGISTRY
    # --------------------------------------------------

    def unregister(self, name: str) -> None:
        with self._lock:
            if name not in self._tools:
                raise ToolError(f"Tool not found: {name}")
            del self._tools[name]

    def get_handler(self, name: str) -> Callable:
        with self._lock:
            tool = self._tools.get(name)
            if tool is None:
                raise ToolError(f"Unknown tool: {name}")
            return tool.handler

    # --------------------------------------------------
    # MODEL INTERFACE
    # --------------------------------------------------

    def get_tools(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": deepcopy(tool.parameters)
                    }
                }
                for tool in self._tools.values()
            ]

    # --------------------------------------------------
    # ARGUMENT VALIDATION
    # --------------------------------------------------

    def _validate_value(self, value, schema: dict, path: str = "arguments"):
        expected = schema.get("type")

        type_checks = {
            "string":  lambda v: isinstance(v, str),
            "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
            "number":  lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
            "boolean": lambda v: isinstance(v, bool),
            "object":  lambda v: isinstance(v, dict),
            "array":   lambda v: isinstance(v, list),
        }

        if expected not in type_checks:
            raise ToolError(f"Unsupported schema type at {path}: {expected}")

        if not type_checks[expected](value):
            raise ToolError(f"Invalid type at {path}. Expected {expected}.")

        # Enum validation
        if "enum" in schema and value not in schema["enum"]:
            raise ToolError(f"Invalid value at {path}.")

        # String constraints
        if expected == "string":
            if len(value) < schema.get("minLength", 0):
                raise ToolError(f"String too short at {path}.")

            if len(value) > schema.get("maxLength", float("inf")):
                raise ToolError(f"String too long at {path}.")

        # Numeric constraints
        if expected in ("integer", "number"):
            if "minimum" in schema and value < schema["minimum"]:
                raise ToolError(f"Value below minimum at {path}.")

            if "maximum" in schema and value > schema["maximum"]:
                raise ToolError(f"Value above maximum at {path}.")

        # Object validation
        if expected == "object":
            properties = schema.get("properties", {})
            required = schema.get("required", [])

            for key in required:
                if key not in value:
                    raise ToolError(f"Missing required argument: {path}.{key}")

            for key, item in value.items():
                if key not in properties:
                    if schema.get("additionalProperties", True) is False:
                        raise ToolError(f"Unexpected argument: {path}.{key}")
                    continue

                self._validate_value(
                    item,
                    properties[key],
                    f"{path}.{key}"
                )

        # Array validation
        if expected == "array" and "items" in schema:
            for index, item in enumerate(value):
                self._validate_value(
                    item,
                    schema["items"],
                    f"{path}[{index}]"
                )

    def _validate_arguments(self, arguments: dict, schema: dict):
        if not isinstance(arguments, dict):
            raise ToolError("Tool arguments must be a JSON object.")
        self._validate_value(arguments, schema)

    # --------------------------------------------------
    # EXECUTION
    # --------------------------------------------------

    def execute(self, name: str, arguments: dict) -> dict:
        with self._lock:
            tool = self._tools.get(name)

        if tool is None:
            return {
                "ok": False,
                "tool": name,
                "error": f"Unknown tool: {name}"
            }

        try:
            self._validate_arguments(arguments, tool.parameters)

            result = tool.handler(**arguments)

            return {
                "ok": True,
                "tool": name,
                "result": result
            }

        except Exception as exc:
            return {
                "ok": False,
                "tool": name,
                "error": f"{type(exc).__name__}: {exc}"
            }

    # --------------------------------------------------
    # INFORMATION
    # --------------------------------------------------

    def list_tools(self) -> list[str]:
        with self._lock:
            return list(self._tools.keys())

    def __len__(self) -> int:
        with self._lock:
            return len(self._tools)