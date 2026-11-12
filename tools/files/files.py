from pathlib import Path

from core.file_processor import FileProcessingError, FileProcessor
from core.tool_manager   import ToolManager


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MiB safety cap

_BLOCKED_DIRS = frozenset({
    ".ssh", ".gnupg", ".aws", ".kube", ".git", ".pki", ".docker",
})
_BLOCKED_NAMES = frozenset({
    ".env", ".netrc", ".bash_history", ".zsh_history",
    ".python_history", "authorized_keys", "known_hosts",
})
_BLOCKED_PREFIXES = ("id_rsa", "id_ed25519", "id_ecdsa", "id_dsa", ".env.", "credentials")
_BLOCKED_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".kdbx", ".jks")


class FileTools:
    """Provides local document reading and OCR inspection tools."""

    DEFAULT_CHUNK_CHARS = 1600
    MIN_CHUNK_CHARS = 200
    MAX_CHUNK_CHARS = 1800

    @staticmethod
    def _validate_path(raw_path: str) -> Path:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise FileProcessingError("File path cannot be empty.")

        candidate = Path(raw_path.strip()).expanduser()
        if not candidate.is_absolute():
            candidate = PROJECT_ROOT / candidate

        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            if not Path(raw_path.strip()).is_absolute() and "/" not in raw_path.strip() and "\\" not in raw_path.strip():
                raise FileProcessingError(
                    f"File not found on disk: '{raw_path}'. "
                    "If this is an uploaded or pasted chat attachment, its extracted/OCR text is already provided in [ATTACHED FILE CONTENT] inside the prompt — do not call read_file for attachments."
                ) from exc
            raise FileProcessingError(f"File not found: {raw_path}") from exc

        if not resolved.is_file():
            raise FileProcessingError(f"Path is not a regular file: {raw_path}")

        home_root = Path.home().resolve()
        allowed = False
        for root in (home_root, PROJECT_ROOT):
            try:
                resolved.relative_to(root)
                allowed = True
                break
            except ValueError:
                continue

        if not allowed:
            raise FileProcessingError(
                f"Access denied: '{resolved}' is outside permitted home/workspace directories."
            )

        lowered_parts = {part.lower() for part in resolved.parts[:-1]}
        if lowered_parts & _BLOCKED_DIRS:
            raise FileProcessingError(
                f"Access denied: sensitive directory blocked for '{resolved.name}'."
            )

        name_lower = resolved.name.lower()
        if (
            name_lower in _BLOCKED_NAMES
            or name_lower.startswith(_BLOCKED_PREFIXES)
            or name_lower.endswith(_BLOCKED_SUFFIXES)
        ):
            raise FileProcessingError(
                f"Access denied: sensitive file blocked ('{resolved.name}')."
            )

        size = resolved.stat().st_size
        if size > MAX_FILE_BYTES:
            raise FileProcessingError(
                f"File '{resolved.name}' exceeds 50 MiB limit ({size} bytes)."
            )

        return resolved

    def read_file(
        self,
        path: str,
        page_start: int | None = None,
        page_end: int | None = None,
        offset: int = 0,
        max_chars: int = DEFAULT_CHUNK_CHARS,
    ) -> dict:
        """Read and extract readable text (with OCR fallback) from a local file."""
        resolved = self._validate_path(path)

        if page_start is not None and page_start < 1:
            raise FileProcessingError("page_start must be >= 1.")
        if page_end is not None and page_start is not None and page_end < page_start:
            raise FileProcessingError("page_end must be >= page_start.")

        safe_offset = max(0, int(offset))
        safe_max = min(max(int(max_chars), self.MIN_CHUNK_CHARS), self.MAX_CHUNK_CHARS)

        doc = FileProcessor.extract_path(
            resolved,
            max_chars=0,  # Full extraction, paginated below
            page_start=page_start,
            page_end=page_end,
        )

        full_text = doc.text
        total_chars = len(full_text)
        if safe_offset >= total_chars and total_chars > 0:
            slice_text = ""
            has_more = False
            next_offset = None
        else:
            slice_text = full_text[safe_offset : safe_offset + safe_max]
            next_idx = safe_offset + len(slice_text)
            has_more = next_idx < total_chars
            next_offset = next_idx if has_more else None

        # Keep metadata compact so JSON stays well below agent 2400-char cap
        compact_meta = {}
        for key in ("pages", "pages_processed", "text_pages", "ocr_pages", "width", "height", "ocr_confidence", "slides", "rows", "chapters"):
            if key in doc.meta and doc.meta[key] is not None:
                compact_meta[key] = doc.meta[key]
        if "sheets" in doc.meta and isinstance(doc.meta["sheets"], list):
            sheets = doc.meta["sheets"]
            compact_meta["sheets"] = sheets[:8] if len(sheets) > 8 else sheets

        return {
            "filename": doc.filename,
            "path": str(resolved),
            "format": doc.format,
            "method": doc.method,
            "offset": safe_offset,
            "returned_chars": len(slice_text),
            "total_chars": total_chars,
            "has_more": has_more,
            "next_offset": next_offset,
            "meta": compact_meta,
            "content": slice_text,
        }


_fT = FileTools()


def register(tool_manager: ToolManager):
    tool_manager.register(
        name="read_file",
        description=(
            "Read and extract text from an existing LOCAL FILESYSTEM PATH (images via OCR, PDFs, "
            "Markdown, plain text, code, CSV, HTML, DOCX, XLSX, PPTX, ODT/ODS/ODP, RTF, EPUB, "
            "and legacy DOC/XLS/PPT). ONLY use this when the user gives a local file path on disk; "
            "NEVER call this for files already provided under [ATTACHED FILE CONTENT] in the prompt. "
            "Supports character-offset pagination and PDF page ranges for large files."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Local file path (relative to workspace or absolute under home directory)."
                },
                "page_start": {
                    "type": "integer",
                    "description": "Optional starting 1-based page number for PDFs.",
                    "minimum": 1
                },
                "page_end": {
                    "type": "integer",
                    "description": "Optional ending 1-based page number for PDFs.",
                    "minimum": 1
                },
                "offset": {
                    "type": "integer",
                    "description": "Character offset to resume reading long extracted text (default 0).",
                    "minimum": 0
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Maximum characters to return per call (200-1800, default 1600).",
                    "minimum": 200,
                    "maximum": 1800
                }
            },
            "required": ["path"],
            "additionalProperties": False
        },
        handler=_fT.read_file
    )
