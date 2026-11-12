# This script is AI-written, has not been verified, and is therefore flagged for verification.

import json
import sqlite3
import threading

from pathlib  import Path
from datetime import datetime, timezone

from core.config import config


class ConversationError(Exception):
    """Raised when a conversation operation fails"""
    ...


class ConversationManager:
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

            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (

                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    title       TEXT NOT NULL DEFAULT 'New conversation',
                    summary     TEXT,

                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL

                );

                CREATE TABLE IF NOT EXISTS messages (

                    id              INTEGER PRIMARY KEY AUTOINCREMENT,

                    conversation_id INTEGER NOT NULL,

                    role            TEXT NOT NULL,
                    content         TEXT NOT NULL,
                    attachments     TEXT,

                    model           TEXT,
                    created_at      TEXT NOT NULL,

                    FOREIGN KEY (conversation_id)
                        REFERENCES conversations(id)
                        ON DELETE CASCADE,

                    CHECK (role IN ('system', 'user', 'assistant'))

                );

                CREATE INDEX IF NOT EXISTS
                    idx_messages_conversation

                ON messages(conversation_id, id);
            """)

            # Ensure summary column exists in existing databases
            cols = [row[1] for row in self.conn.execute("PRAGMA table_info(conversations)").fetchall()]
            if "summary" not in cols:
                self.conn.execute("ALTER TABLE conversations ADD COLUMN summary TEXT")

            # Ensure attachments column exists in existing databases
            msg_cols = [row[1] for row in self.conn.execute("PRAGMA table_info(messages)").fetchall()]
            if "attachments" not in msg_cols:
                self.conn.execute("ALTER TABLE messages ADD COLUMN attachments TEXT")

            self.conn.commit()

    # --------------------------------------------------
    # CONVERSATIONS
    # --------------------------------------------------

    def create_conversation(
        self,
        title: str = "New conversation"
    ) -> int:

        now = self._timestamp()

        with self.lock:

            cursor = self.conn.execute("""
                INSERT INTO conversations (
                    title,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?)
            """, (title, now, now))

            self.conn.commit()

            return cursor.lastrowid

    def get_conversation(self, conversation_id: int):

        with self.lock:

            row = self.conn.execute("""
                SELECT *
                FROM conversations
                WHERE id = ?
            """, (conversation_id,)).fetchone()

            return dict(row) if row else None

    def list_conversations(self):

        with self.lock:

            rows = self.conn.execute("""
                SELECT *
                FROM conversations
                ORDER BY updated_at DESC, id DESC
            """).fetchall()

            return [dict(row) for row in rows]

    def rename_conversation(
        self,
        conversation_id: int,
        title: str
    ):

        with self.lock:

            cursor = self.conn.execute("""
                UPDATE conversations
                SET title = ?, updated_at = ?
                WHERE id = ?
            """, (
                title,
                self._timestamp(),
                conversation_id
            ))

            self.conn.commit()

            if cursor.rowcount == 0:
                raise ConversationError(
                    f"Conversation not found: {conversation_id}"
                )

    def delete_conversation(self, conversation_id: int):

        with self.lock:

            cursor = self.conn.execute("""
                DELETE FROM conversations
                WHERE id = ?
            """, (conversation_id,))

            self.conn.commit()

            if cursor.rowcount == 0:
                raise ConversationError(
                    f"Conversation not found: {conversation_id}"
                )

    def get_summary(self, conversation_id: int) -> str | None:
        with self.lock:
            row = self.conn.execute("""
                SELECT summary
                FROM conversations
                WHERE id = ?
            """, (conversation_id,)).fetchone()

            return row["summary"] if row and row["summary"] else None

    def update_summary(self, conversation_id: int, summary: str | None):
        with self.lock:
            cursor = self.conn.execute("""
                UPDATE conversations
                SET summary = ?, updated_at = ?
                WHERE id = ?
            """, (summary, self._timestamp(), conversation_id))

            self.conn.commit()

            if cursor.rowcount == 0:
                raise ConversationError(
                    f"Conversation not found: {conversation_id}"
                )

    # --------------------------------------------------
    # MESSAGES
    # --------------------------------------------------

    def add_message(
        self,
        conversation_id: int,
        role: str,
        content: str,
        model: str | None = None,
        attachments: list[dict] | None = None
    ) -> int:

        if role not in ("system", "user", "assistant"):
            raise ConversationError(
                f"Invalid message role: {role}"
            )

        cleaned_attachments = [
            att for att in (attachments or [])
            if isinstance(att, dict)
        ]

        if not isinstance(content, str) or (not content.strip() and not cleaned_attachments):
            raise ConversationError(
                "Message content cannot be empty."
            )

        attachments_json = (
            json.dumps(cleaned_attachments, ensure_ascii=False)
            if cleaned_attachments
            else None
        )

        now = self._timestamp()

        with self.lock:

            try:

                with self.conn:

                    conversation = self.conn.execute("""
                        SELECT title
                        FROM conversations
                        WHERE id = ?
                    """, (conversation_id,)).fetchone()

                    if conversation is None:
                        raise ConversationError(
                            f"Conversation not found: {conversation_id}"
                        )

                    cursor = self.conn.execute("""
                        INSERT INTO messages (
                            conversation_id,
                            role,
                            content,
                            attachments,
                            model,
                            created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        conversation_id,
                        role,
                        content,
                        attachments_json,
                        model,
                        now
                    ))

                    # Automatically title new conversations.
                    if (
                        role == "user"
                        and conversation["title"] == "New conversation"
                    ):

                        if content.strip():
                            title = " ".join(content.split())[:60]
                        else:
                            first_name = cleaned_attachments[0].get("filename", "attachment")
                            title = f"File: {first_name}"[:60]

                        self.conn.execute("""
                            UPDATE conversations
                            SET title = ?
                            WHERE id = ?
                        """, (title, conversation_id))

                    self.conn.execute("""
                        UPDATE conversations
                        SET updated_at = ?
                        WHERE id = ?
                    """, (now, conversation_id))

                    return cursor.lastrowid

            except sqlite3.Error as exc:

                raise ConversationError(
                    f"Failed to save message: {exc}"
                ) from exc

    def get_messages(
        self,
        conversation_id: int,
        limit: int | None = None
    ) -> list[dict]:

        with self.lock:

            if limit is not None:

                if limit < 1:
                    raise ValueError("limit must be positive.")

                rows = self.conn.execute("""
                    SELECT *
                    FROM (
                        SELECT *
                        FROM messages
                        WHERE conversation_id = ?
                        ORDER BY id DESC
                        LIMIT ?
                    )
                    ORDER BY id ASC
                """, (conversation_id, limit)).fetchall()

            else:

                rows = self.conn.execute("""
                    SELECT *
                    FROM messages
                    WHERE conversation_id = ?
                    ORDER BY id ASC
                """, (conversation_id,)).fetchall()

            messages = []
            for row in rows:
                item = dict(row)
                raw_att = item.get("attachments")
                if raw_att:
                    try:
                        parsed = json.loads(raw_att)
                        item["attachments"] = parsed if isinstance(parsed, list) else []
                    except json.JSONDecodeError:
                        item["attachments"] = []
                else:
                    item["attachments"] = []
                messages.append(item)

            return messages

    def get_message_count(self, conversation_id: int) -> int:

        with self.lock:

            row = self.conn.execute("""
                SELECT COUNT(*)
                FROM messages
                WHERE conversation_id = ?
            """, (conversation_id,)).fetchone()

            return row[0]

    # --------------------------------------------------
    # LIFECYCLE
    # --------------------------------------------------

    def close(self):

        with self.lock:

            if self.conn is not None:

                self.conn.close()
                self.conn = None