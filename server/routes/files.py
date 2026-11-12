from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from core.file_processor import (
    DEFAULT_ATTACHMENT_MAX_CHARS,
    FileProcessingError,
    FileProcessor,
    SUPPORTED_EXTENSIONS,
)


router = APIRouter(prefix="/api/files", tags=["Files"])

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MiB
CHUNK_SIZE = 1024 * 1024


@router.get("/formats")
def get_supported_formats():
    return {
        "extensions": SUPPORTED_EXTENSIONS,
        "max_upload_bytes": MAX_UPLOAD_BYTES,
        "default_max_chars": DEFAULT_ATTACHMENT_MAX_CHARS,
    }


@router.post("/extract")
async def extract_file(
    request: Request,
    file: UploadFile = File(...),
    page_start: int | None = Form(default=None),
    page_end: int | None = Form(default=None),
):
    filename = file.filename or "attachment"

    if page_start is not None and page_start < 1:
        raise HTTPException(status_code=400, detail="page_start must be >= 1.")
    if page_end is not None and page_start is not None and page_end < page_start:
        raise HTTPException(status_code=400, detail="page_end must be >= page_start.")

    chunks: list[bytes] = []
    total_size = 0
    while True:
        chunk = await file.read(CHUNK_SIZE)
        if not chunk:
            break
        total_size += len(chunk)
        if total_size > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"File '{filename}' exceeds the 25 MiB upload limit."
            )
        chunks.append(chunk)

    data = b"".join(chunks)
    if not data:
        raise HTTPException(status_code=400, detail=f"File '{filename}' is empty.")

    try:
        doc = FileProcessor.extract_bytes(
            data=data,
            filename=filename,
            max_chars=DEFAULT_ATTACHMENT_MAX_CHARS,
            page_start=page_start,
            page_end=page_end,
        )
    except FileProcessingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process '{filename}': {type(exc).__name__}: {exc}"
        ) from exc

    payload = doc.to_dict()

    engine = getattr(request.app.state, "engine", None)
    if engine is not None and getattr(engine, "context", None) is not None:
        try:
            payload["estimated_tokens"] = engine.context.estimate_tokens(doc.text)
        except Exception:
            payload["estimated_tokens"] = max(1, len(doc.text) // 4)
    else:
        payload["estimated_tokens"] = max(1, len(doc.text) // 4)

    return payload
