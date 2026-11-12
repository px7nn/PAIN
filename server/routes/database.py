import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from core.memory import MemoryError, MemoryManager
from server.schemas import CreateMemoryRequest, UpdateMemoryRequest


router = APIRouter(prefix="/api/database", tags=["Database"])

PRIMARY_TABLES = {"memories", "conversations", "messages"}


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _serialize_cell(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw_bytes = bytes(value)
        return f"<BLOB {len(raw_bytes)} bytes>"
    return value


def _get_valid_tables(conn: sqlite3.Connection) -> dict[str, dict]:
    rows = conn.execute(
        """
        SELECT name, type, sql
        FROM sqlite_master
        WHERE type IN ('table', 'view')
        ORDER BY name ASC
        """
    ).fetchall()
    return {
        row["name"]: {
            "name": row["name"],
            "type": row["type"],
            "sql": row["sql"] or "",
        }
        for row in rows
    }


def _db_file_sizes(db_path: Path) -> dict[str, int]:
    wal_path = Path(f"{db_path}-wal")
    shm_path = Path(f"{db_path}-shm")

    db_bytes = db_path.stat().st_size if db_path.is_file() else 0
    wal_bytes = wal_path.stat().st_size if wal_path.is_file() else 0
    shm_bytes = shm_path.stat().st_size if shm_path.is_file() else 0
    return {
        "db_bytes": db_bytes,
        "wal_bytes": wal_bytes,
        "shm_bytes": shm_bytes,
        "total_bytes": db_bytes + wal_bytes + shm_bytes,
    }


@router.get("/overview")
def database_overview(request: Request):
    """Return SQLite table metadata, memory stats, and mounted runtime/tool context."""
    engine = request.app.state.engine
    conv = engine.conversations
    mem = engine.memory

    with conv.lock:
        valid_tables = _get_valid_tables(conv.conn)
        tables = []
        for name, meta in valid_tables.items():
            try:
                count_row = conv.conn.execute(
                    f"SELECT COUNT(*) FROM {_quote_ident(name)}"
                ).fetchone()
                row_count = int(count_row[0]) if count_row else 0
            except sqlite3.Error:
                row_count = None

            tables.append({
                "name": name,
                "type": meta["type"],
                "row_count": row_count,
                "internal": name not in PRIMARY_TABLES,
            })

        tables.sort(key=lambda item: (item["internal"], item["name"]))

        conv_count = conv.conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        msg_count = conv.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

    with mem.lock:
        mem_total = mem.count()
        mem_pinned = mem.conn.execute(
            "SELECT COUNT(*) FROM memories WHERE pinned = 1"
        ).fetchone()[0]
        mem_auto = mem.conn.execute(
            "SELECT COUNT(*) FROM memories WHERE pinned = 1 AND importance >= 4"
        ).fetchone()[0]
        cat_rows = mem.conn.execute(
            "SELECT category, COUNT(*) AS cnt FROM memories GROUP BY category"
        ).fetchall()
        by_category = {row["category"]: int(row["cnt"]) for row in cat_rows}

    active_model = engine.current_model
    personality_preview = ""
    try:
        personality_preview = engine.context.load_personality(active_model)
    except Exception:
        personality_preview = ""

    raw_tools = engine.tool_manager.get_tools()
    loaded_tools = [
        {
            "name": t.get("function", {}).get("name", ""),
            "description": t.get("function", {}).get("description", ""),
            "parameters": list(
                (t.get("function", {}).get("parameters", {}).get("properties") or {}).keys()
            ),
        }
        for t in raw_tools
    ]

    model_status = engine.models.status()
    db_path = Path(conv.db_path)

    return {
        "database": {
            "path": str(db_path),
            **_db_file_sizes(db_path),
            "table_count": len(tables),
            "conversations_count": int(conv_count),
            "messages_count": int(msg_count),
        },
        "tables": tables,
        "memories": {
            "total": int(mem_total),
            "pinned": int(mem_pinned),
            "auto_injected": int(mem_auto),
            "categories": sorted(MemoryManager.CATEGORIES),
            "by_category": by_category,
        },
        "runtime": {
            "active_model": active_model,
            "inference_running": bool(model_status.get("running")),
            "server_url": model_status.get("server_url"),
            "loaded_tools": loaded_tools,
            "failed_tools": engine.tool_loader.failed(),
            "personality_preview": personality_preview,
        },
    }


@router.get("/tables/{table_name}")
def browse_table(
    table_name: str,
    request: Request,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    q: str | None = Query(default=None),
):
    """Browse schema and paginated rows for any SQLite table in pain.db."""
    engine = request.app.state.engine
    conv = engine.conversations

    with conv.lock:
        valid_tables = _get_valid_tables(conv.conn)
        if table_name not in valid_tables:
            raise HTTPException(status_code=404, detail=f"Table not found: {table_name}")

        quoted = _quote_ident(table_name)
        table_info_rows = conv.conn.execute(f"PRAGMA table_info({quoted})").fetchall()
        schema = [
            {
                "cid": r["cid"],
                "name": r["name"],
                "type": r["type"] or "ANY",
                "notnull": bool(r["notnull"]),
                "default": r["dflt_value"],
                "pk": bool(r["pk"]),
            }
            for r in table_info_rows
        ]
        col_names = [col["name"] for col in schema]

        total_rows = int(conv.conn.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0])

        where_sql = ""
        params: list[Any] = []
        clean_q = (q or "").strip()
        if clean_q and col_names:
            like_clauses = [
                f"CAST({_quote_ident(col)} AS TEXT) LIKE ?"
                for col in col_names
            ]
            where_sql = " WHERE " + " OR ".join(like_clauses)
            params.extend([f"%{clean_q}%"] * len(col_names))

        filtered_rows = int(
            conv.conn.execute(
                f"SELECT COUNT(*) FROM {quoted}{where_sql}",
                params,
            ).fetchone()[0]
        )

        # Prefer newest rows first when rowid exists; fall back gracefully on WITHOUT ROWID tables.
        rows_raw = []
        if "id" in col_names:
            order_sql = ' ORDER BY "id" DESC'
        else:
            order_sql = " ORDER BY rowid DESC"

        try:
            rows_raw = conv.conn.execute(
                f"SELECT * FROM {quoted}{where_sql}{order_sql} LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        except sqlite3.OperationalError:
            rows_raw = conv.conn.execute(
                f"SELECT * FROM {quoted}{where_sql} LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()

        serialized_rows = [
            {col: _serialize_cell(row[col]) for col in row.keys()}
            for row in rows_raw
        ]

        return {
            "table": table_name,
            "sql": valid_tables[table_name]["sql"],
            "schema": schema,
            "total_rows": total_rows,
            "filtered_rows": filtered_rows,
            "limit": limit,
            "offset": offset,
            "rows": serialized_rows,
        }


@router.get("/memories")
def list_memories(
    request: Request,
    category: str | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
):
    """List and filter model memories."""
    mem = request.app.state.engine.memory
    clean_category = (category or "").strip() or None
    clean_q = (q or "").strip().lower()

    try:
        items = mem.list(category=clean_category, limit=500 if clean_q else limit)
    except MemoryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if clean_q:
        matched = []
        for item in items:
            haystack = " ".join([
                f"#{item['id']}",
                item.get("category") or "",
                item.get("content") or "",
                item.get("source") or "",
                " ".join(item.get("tags") or []),
            ]).lower()
            if clean_q in haystack:
                matched.append(item)
                if len(matched) >= limit:
                    break
        items = matched

    return {
        "memories": items,
        "count": len(items),
        "total": mem.count(),
        "categories": sorted(MemoryManager.CATEGORIES),
    }


@router.post("/memories")
def create_memory(payload: CreateMemoryRequest, request: Request):
    """Manually add a persistent memory."""
    mem = request.app.state.engine.memory
    try:
        mem_id = mem.add(
            category=payload.category,
            content=payload.content,
            source=payload.source or "manual",
            importance=payload.importance,
            pinned=payload.pinned,
            tags=payload.tags,
        )
        return {"ok": True, "memory": mem.get(mem_id)}
    except MemoryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/memories/{memory_id}")
def update_memory(
    memory_id: int,
    payload: UpdateMemoryRequest,
    request: Request,
):
    """Update an existing memory (content, category, importance, pinned, or tags)."""
    mem = request.app.state.engine.memory
    try:
        mem.update(
            memory_id=memory_id,
            content=payload.content,
            category=payload.category,
            importance=payload.importance,
            pinned=payload.pinned,
            tags=payload.tags,
        )
        return {"ok": True, "memory": mem.get(memory_id)}
    except MemoryError as exc:
        status = 404 if "not found" in str(exc).lower() else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.delete("/memories/{memory_id}")
def delete_memory(memory_id: int, request: Request):
    """Delete a specific memory."""
    mem = request.app.state.engine.memory
    try:
        mem.delete(memory_id)
        return {"ok": True, "deleted_id": memory_id}
    except MemoryError as exc:
        status = 404 if "not found" in str(exc).lower() else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.delete("/memories")
def clear_unpinned_memories(
    request: Request,
    category: str | None = Query(default=None),
):
    """Clear all non-pinned memories (optionally scoped to a category)."""
    mem = request.app.state.engine.memory
    clean_category = (category or "").strip() or None
    try:
        mem.clear(category=clean_category)
        return {"ok": True, "remaining": mem.count()}
    except MemoryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
