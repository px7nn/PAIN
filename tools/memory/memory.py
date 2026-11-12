from core.memory       import MemoryManager, MemoryError
from core.tool_manager import ToolManager


CATEGORY_ENUM = sorted(MemoryManager.CATEGORIES)


class MemoryTools:
    """Provides persistent memory search, creation, update, and deletion tools."""

    MIN_CONTENT_LENGTH = 3

    def __init__(self, memory: MemoryManager | None = None):
        self.memory = memory or MemoryManager()

    @staticmethod
    def _format_memory(item: dict) -> dict:
        return {
            "id": item["id"],
            "category": item["category"],
            "content": item["content"],
            "importance": item["importance"],
            "pinned": bool(item["pinned"]),
            "tags": item.get("tags", []),
        }

    def _validate_strict_content(self, content: str) -> str:
        cleaned = self.memory._validate_content(content)
        if len(cleaned) < self.MIN_CONTENT_LENGTH:
            raise MemoryError(
                f"Memory content must be at least {self.MIN_CONTENT_LENGTH} non-whitespace characters."
            )
        return cleaned

    def search_memory(
        self,
        query: str,
        category: str | None = None,
        limit: int = 5
    ) -> dict:
        """Search stored memories using FTS5 BM25 or keyword fallback."""
        clamped_limit = min(max(1, int(limit)), 20)
        results = self.memory.search(
            query=query,
            category=category or None,
            limit=clamped_limit
        )
        memories = [self._format_memory(m) for m in results]

        if not memories:
            return {
                "count": 0,
                "memories": [],
                "message": f"No memories found matching '{query.strip()}'."
            }

        return {
            "count": len(memories),
            "memories": memories
        }

    def save_memory(
        self,
        content: str,
        category: str,
        importance: int = 3,
        pinned: bool = False,
        tags: list[str] | None = None
    ) -> dict:
        """Strictly validate, deduplicate, and persist a memory."""
        cleaned = self._validate_strict_content(content)
        self.memory._validate_category(category)
        importance = self.memory._validate_importance(int(importance))
        pinned = self.memory._validate_pinned(bool(pinned))
        cleaned_tags = self.memory._validate_tags(tags)

        existing = self.memory.find_duplicate(cleaned)
        if existing is not None:
            merged_tags = self.memory._validate_tags(existing.get("tags", []) + cleaned_tags)
            new_importance = max(existing["importance"], importance)
            new_pinned = bool(existing["pinned"]) or pinned

            changed = (
                new_importance != existing["importance"]
                or new_pinned != bool(existing["pinned"])
                or merged_tags != existing.get("tags", [])
            )
            if changed:
                self.memory.update(
                    existing["id"],
                    importance=new_importance,
                    pinned=new_pinned,
                    tags=merged_tags,
                    source="agent"
                )
                existing = self.memory.get(existing["id"]) or existing

            return {
                "saved": True,
                "id": existing["id"],
                "category": existing["category"],
                "deduplicated": True,
                "updated": changed,
                "memory": self._format_memory(existing),
                "message": (
                    f"Memory #{existing['id']} already exists"
                    + ("; updated metadata." if changed else ".")
                )
            }

        memory_id = self.memory.add(
            category=category,
            content=cleaned,
            source="agent",
            importance=importance,
            pinned=pinned,
            tags=cleaned_tags
        )
        created = self.memory.get(memory_id)

        return {
            "saved": True,
            "id": memory_id,
            "category": category,
            "deduplicated": False,
            "memory": self._format_memory(created) if created else None,
            "message": f"Successfully stored memory #{memory_id} under '{category}'."
        }

    def update_memory(
        self,
        memory_id: int,
        content: str | None = None,
        category: str | None = None,
        importance: int | None = None,
        pinned: bool | None = None,
        tags: list[str] | None = None
    ) -> dict:
        """Modify an existing memory record by ID."""
        if (
            content is None
            and category is None
            and importance is None
            and pinned is None
            and tags is None
        ):
            raise MemoryError(
                "At least one field (content, category, importance, pinned, or tags) must be provided to update."
            )

        cleaned_content = self._validate_strict_content(content) if content is not None else None
        self.memory.update(
            memory_id=memory_id,
            content=cleaned_content,
            category=category,
            importance=importance,
            pinned=pinned,
            tags=tags,
            source="agent"
        )
        updated = self.memory.get(memory_id)

        return {
            "updated": True,
            "id": memory_id,
            "memory": self._format_memory(updated) if updated else None,
            "message": f"Successfully updated memory #{memory_id}."
        }

    def delete_memory(self, memory_id: int) -> dict:
        """Delete a stored memory record by ID."""
        existing = self.memory.get(memory_id)
        if existing is None:
            raise MemoryError(f"Memory not found: {memory_id}")

        self.memory.delete(memory_id)

        return {
            "deleted": True,
            "id": memory_id,
            "deleted_content": existing["content"],
            "message": f"Successfully deleted memory #{memory_id}."
        }


def register(tool_manager: ToolManager, memory: MemoryManager | None = None):
    """Register memory tools with the ToolManager."""
    memory_tools = MemoryTools(memory=memory)

    category_prop = {
        "type": "string",
        "enum": CATEGORY_ENUM,
        "description": "Memory category classification."
    }
    importance_prop = {
        "type": "integer",
        "minimum": 1,
        "maximum": 5,
        "description": "Importance from 1 (minor) to 5 (critical core fact)."
    }
    pinned_prop = {
        "type": "boolean",
        "description": "Pin memory so high-priority facts stay injected across sessions."
    }
    tags_prop = {
        "type": "array",
        "items": {"type": "string"},
        "description": "Optional tags for organizing this memory."
    }

    tool_manager.register(
        name="search_memory",
        description=(
            "Search persistent memory for user facts, preferences, project details, decisions, "
            "or memory IDs before updating/deleting. Call whenever prior context is relevant."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Keywords or phrase to search in persistent memory."
                },
                "category": {**category_prop, "description": "Optional category filter."},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "description": "Maximum memories to return (default: 5)."
                }
            },
            "required": ["query"],
            "additionalProperties": False
        },
        handler=memory_tools.search_memory
    )

    tool_manager.register(
        name="save_memory",
        description=(
            "STRICTLY REQUIRED whenever the user states a personal fact, preference, workflow rule, "
            "hardware/OS detail, project convention, decision, ongoing task, or says 'remember'. "
            "Never say 'noted' or 'I will remember' without calling save_memory (or update_memory)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "minLength": MemoryTools.MIN_CONTENT_LENGTH,
                    "description": "Concise, self-contained atomic fact, preference, or decision to persist."
                },
                "category": category_prop,
                "importance": importance_prop,
                "pinned": pinned_prop,
                "tags": tags_prop
            },
            "required": ["content", "category"],
            "additionalProperties": False
        },
        handler=memory_tools.save_memory
    )

    tool_manager.register(
        name="update_memory",
        description=(
            "Update an existing memory by ID (from search_memory or [#id] in context) when a user "
            "fact, preference, or task changes, needs correction, or needs metadata adjusted."
        ),
        parameters={
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "ID of the existing memory to update."
                },
                "content": {
                    "type": "string",
                    "minLength": MemoryTools.MIN_CONTENT_LENGTH,
                    "description": "Replacement memory content."
                },
                "category": category_prop,
                "importance": importance_prop,
                "pinned": pinned_prop,
                "tags": tags_prop
            },
            "required": ["memory_id"],
            "additionalProperties": False
        },
        handler=memory_tools.update_memory
    )

    tool_manager.register(
        name="delete_memory",
        description=(
            "Delete a persistent memory by ID when the user asks to forget it or when a stored "
            "fact or task is wrong or obsolete."
        ),
        parameters={
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "ID of the memory to permanently delete."
                }
            },
            "required": ["memory_id"],
            "additionalProperties": False
        },
        handler=memory_tools.delete_memory
    )
