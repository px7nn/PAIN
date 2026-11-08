# This script is AI-written, has not been verified, and is therefore flagged for verification.

import sqlite3
import threading
import json

from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from core.config import config


class MemoryError(Exception):
    """Raised when a memory operation fails."""
    pass


class MemoryManager:

    CATEGORIES = {
        "fact",
        "project",
        "preference",
        "environment",
        "decision",
        "task"
    }

    def __init__(self, db_path: Path | None = None):

        self.db_path = Path(
            db_path or config.data_dir / "pain.db"
        )

        self.db_path.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        self.lock = threading.RLock()

        self.conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            timeout=30
        )

        self.conn.row_factory = sqlite3.Row

        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = NORMAL")
        self.conn.execute("PRAGMA busy_timeout = 30000")

        self._initialize()

    @staticmethod
    def _timestamp() -> str:

        return datetime.now(timezone.utc).isoformat()

    def _initialize(self):

        with self.lock:

            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (

                    id          INTEGER PRIMARY KEY AUTOINCREMENT,

                    category    TEXT NOT NULL,
                    content     TEXT NOT NULL,

                    source      TEXT,
                    importance  INTEGER NOT NULL DEFAULT 3,
                    pinned      INTEGER NOT NULL DEFAULT 0,

                    tags        TEXT NOT NULL DEFAULT '[]',

                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL,

                    CHECK (importance BETWEEN 1 AND 5),
                    CHECK (pinned IN (0, 1))

                )
            """)

            self.conn.execute("""
                CREATE INDEX IF NOT EXISTS
                    idx_memories_category
                ON memories(category)
            """)

            self.conn.execute("""
                CREATE INDEX IF NOT EXISTS
                    idx_memories_importance
                ON memories(importance DESC)
            """)

            self.conn.commit()

    # --------------------------------------------------
    # VALIDATION
    # --------------------------------------------------

    def _validate_category(self, category: str):

        if category not in self.CATEGORIES:
            raise MemoryError(
                f"Invalid memory category: {category}"
            )

    @staticmethod
    def _validate_content(content: str):

        if not isinstance(content, str) or not content.strip():
            raise MemoryError(
                "Memory content cannot be empty."
            )

    # --------------------------------------------------
    # CREATE
    # --------------------------------------------------

    def add(
        self,
        category: str,
        content: str,
        source: str | None = None,
        importance: int = 3,
        pinned: bool = False,
        tags: list[str] | None = None
    ) -> int:

        self._validate_category(category)
        self._validate_content(content)

        if type(importance) is not int or not 1 <= importance <= 5:
            raise MemoryError(
                "Importance must be an integer between 1 and 5."
            )

        if type(pinned) is not bool:
            raise MemoryError("pinned must be a boolean.")

        if tags is None:
            tags = []

        if not isinstance(tags, list) or not all(
            isinstance(tag, str) for tag in tags
        ):
            raise MemoryError("Tags must be a list of strings.")

        now = self._timestamp()

        with self.lock:

            try:

                with self.conn:

                    cursor = self.conn.execute("""
                        INSERT INTO memories (
                            category,
                            content,
                            source,
                            importance,
                            pinned,
                            tags,
                            created_at,
                            updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        category,
                        content.strip(),
                        source,
                        importance,
                        int(pinned),
                        json.dumps(tags),
                        now,
                        now
                    ))

                    return cursor.lastrowid

            except sqlite3.Error as exc:

                raise MemoryError(
                    f"Failed to save memory: {exc}"
                ) from exc

    # --------------------------------------------------
    # READ
    # --------------------------------------------------

    def get(self, memory_id: int) -> dict | None:

        with self.lock:

            row = self.conn.execute("""
                SELECT *
                FROM memories
                WHERE id = ?
            """, (memory_id,)).fetchone()

            if row is None:
                return None

            return self._deserialize(row)

    def list(
        self,
        category: str | None = None,
        limit: int = 100
    ) -> list[dict]:

        if limit < 1:
            raise MemoryError("Limit must be positive.")

        with self.lock:

            if category is not None:

                self._validate_category(category)

                rows = self.conn.execute("""
                    SELECT *
                    FROM memories
                    WHERE category = ?
                    ORDER BY pinned DESC, importance DESC, updated_at DESC
                    LIMIT ?
                """, (category, limit)).fetchall()

            else:

                rows = self.conn.execute("""
                    SELECT *
                    FROM memories
                    ORDER BY pinned DESC, importance DESC, updated_at DESC
                    LIMIT ?
                """, (limit,)).fetchall()

            return [
                self._deserialize(row)
                for row in rows
            ]

    # --------------------------------------------------
    # SEARCH
    # --------------------------------------------------

    def search(
        self,
        query: str,
        category: str | None = None,
        limit: int = 20
    ) -> list[dict]:

        if not isinstance(query, str) or not query.strip():
            raise MemoryError("Search query cannot be empty.")

        if limit < 1:
            raise MemoryError("Limit must be positive.")

        if category is not None:
            self._validate_category(category)

        terms = query.strip().split()

        conditions = []
        params = []

        for term in terms:

            conditions.append("content LIKE ?")
            params.append(f"%{term}%")

        sql = f"""
            SELECT *
            FROM memories
            WHERE ({" OR ".join(conditions)})
        """

        if category is not None:
            sql += " AND category = ?"
            params.append(category)

        sql += """
            ORDER BY pinned DESC, importance DESC, updated_at DESC
            LIMIT ?
        """

        params.append(limit)

        with self.lock:

            rows = self.conn.execute(
                sql,
                params
            ).fetchall()

            return [
                self._deserialize(row)
                for row in rows
            ]

    # --------------------------------------------------
    # UPDATE
    # --------------------------------------------------

    def update(
        self,
        memory_id: int,
        content: str | None = None,
        category: str | None = None,
        importance: int | None = None,
        pinned: bool | None = None,
        tags: list[str] | None = None,
        source: str | None = None
    ):

        updates = []
        params = []

        if content is not None:

            self._validate_content(content)

            updates.append("content = ?")
            params.append(content.strip())

        if category is not None:

            self._validate_category(category)

            updates.append("category = ?")
            params.append(category)

        if importance is not None:

            if type(importance) is not int or not 1 <= importance <= 5:
                raise MemoryError(
                    "Importance must be an integer between 1 and 5."
                )

            updates.append("importance = ?")
            params.append(importance)

        if pinned is not None:

            if type(pinned) is not bool:
                raise MemoryError("pinned must be a boolean.")

            updates.append("pinned = ?")
            params.append(int(pinned))

        if tags is not None:

            if not isinstance(tags, list) or not all(
                isinstance(tag, str) for tag in tags
            ):
                raise MemoryError("Tags must be a list of strings.")

            updates.append("tags = ?")
            params.append(json.dumps(tags))

        if source is not None:

            updates.append("source = ?")
            params.append(source)

        if not updates:
            return

        updates.append("updated_at = ?")
        params.append(self._timestamp())

        params.append(memory_id)

        sql = f"""
            UPDATE memories
            SET {", ".join(updates)}
            WHERE id = ?
        """

        with self.lock:

            with self.conn:

                cursor = self.conn.execute(sql, params)

                if cursor.rowcount == 0:
                    raise MemoryError(
                        f"Memory not found: {memory_id}"
                    )

    # --------------------------------------------------
    # DELETE
    # --------------------------------------------------

    def delete(self, memory_id: int):

        with self.lock:

            with self.conn:

                cursor = self.conn.execute("""
                    DELETE FROM memories
                    WHERE id = ?
                """, (memory_id,))

                if cursor.rowcount == 0:
                    raise MemoryError(
                        f"Memory not found: {memory_id}"
                    )

    def clear(self, category: str | None = None):

        with self.lock:

            with self.conn:

                if category is None:

                    self.conn.execute("""
                        DELETE FROM memories
                        WHERE pinned = 0
                    """)

                else:

                    self._validate_category(category)

                    self.conn.execute("""
                        DELETE FROM memories
                        WHERE category = ?
                        AND pinned = 0
                    """, (category,))

    # --------------------------------------------------
    # UTILITIES
    # --------------------------------------------------

    def count(self) -> int:

        with self.lock:

            row = self.conn.execute("""
                SELECT COUNT(*)
                FROM memories
            """).fetchone()

            return row[0]

    @staticmethod
    def _deserialize(row: sqlite3.Row) -> dict:

        result = dict(row)

        result["tags"] = json.loads(result["tags"])
        result["pinned"] = bool(result["pinned"])

        return result

    def close(self):

        with self.lock:

            if self.conn is not None:

                self.conn.close()
                self.conn = None