from typing import Any
from core.memory import MemoryManager, MemoryError
from core.tool_manager import ToolManager


class MemoryTools:
    """Provides memory search and persistence tools for the assistant."""

    def __init__(self, memory: MemoryManager | None = None):
        self.memory = memory or MemoryManager()

    def search_memory(
        self,
        query: str,
        category: str | None = None,
        limit: int = 5
    ) -> dict:
        """Search stored memories using full-text search (BM25) or keyword matching."""
        if not query or not query.strip():
            return {
                "count": 0,
                "memories": [],
                "message": "Search query cannot be empty."
            }

        clamped_limit = min(max(1, int(limit)), 20)

        try:
            results = self.memory.search(
                query=query.strip(),
                category=category if category else None,
                limit=clamped_limit
            )

            if not results:
                return {
                    "count": 0,
                    "memories": [],
                    "message": f"No memories found matching '{query}'."
                }

            memories = [
                {
                    "id": m["id"],
                    "category": m["category"],
                    "content": m["content"],
                    "importance": m["importance"],
                    "pinned": bool(m["pinned"]),
                    "tags": m.get("tags", [])
                }
                for m in results
            ]

            return {
                "count": len(memories),
                "memories": memories
            }

        except MemoryError as exc:
            return {
                "count": 0,
                "memories": [],
                "error": str(exc)
            }
        except Exception as exc:
            return {
                "count": 0,
                "memories": [],
                "error": f"Memory search error: {exc}"
            }

    def save_memory(
        self,
        content: str,
        category: str,
        importance: int = 3,
        pinned: bool = False,
        tags: list[str] | None = None
    ) -> dict:
        """Persist a piece of information, user preference, or decision to memory."""
        if not content or not content.strip():
            return {
                "saved": False,
                "error": "Memory content cannot be empty."
            }

        try:
            memory_id = self.memory.add(
                category=category,
                content=content.strip(),
                source="agent",
                importance=int(importance),
                pinned=bool(pinned),
                tags=tags if tags is not None else []
            )

            return {
                "saved": True,
                "id": memory_id,
                "category": category,
                "message": f"Successfully stored memory #{memory_id} under '{category}'."
            }

        except MemoryError as exc:
            return {
                "saved": False,
                "error": str(exc)
            }
        except Exception as exc:
            return {
                "saved": False,
                "error": f"Failed to save memory: {exc}"
            }


_memory_tools = MemoryTools()


def register(tool_manager: ToolManager):
    """Register memory tools with the ToolManager."""
    tool_manager.register(
        name="search_memory",
        description=(
            "Search persistent memory for relevant user preferences, facts, past decisions, "
            "project details, or unfinished tasks. Call this whenever you need context not present "
            "in the current conversation."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keywords, topic, or search phrase to look up in persistent memory."
                },
                "category": {
                    "type": "string",
                    "enum": [
                        "fact",
                        "project",
                        "preference",
                        "environment",
                        "decision",
                        "task"
                    ],
                    "description": "Optional category filter."
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "description": "Maximum number of memories to return (default: 5)."
                }
            },
            "required": ["query"],
            "additionalProperties": False
        },
        handler=_memory_tools.search_memory
    )

    tool_manager.register(
        name="save_memory",
        description=(
            "Store a new fact, user preference, architectural decision, environment detail, "
            "or project context into persistent memory for long-term recall across sessions."
        ),
        parameters={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "minLength": 1,
                    "description": "The specific fact, preference, instruction, or decision to remember."
                },
                "category": {
                    "type": "string",
                    "enum": [
                        "fact",
                        "project",
                        "preference",
                        "environment",
                        "decision",
                        "task"
                    ],
                    "description": "The category classification for this memory."
                },
                "importance": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 5,
                    "description": "Importance score from 1 (minor detail) to 5 (critical core fact). Default is 3."
                },
                "pinned": {
                    "type": "boolean",
                    "description": "Whether to permanently pin this memory so it resists routine cleanup. Default is false."
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags for organizing and categorizing this memory."
                }
            },
            "required": ["content", "category"],
            "additionalProperties": False
        },
        handler=_memory_tools.save_memory
    )
