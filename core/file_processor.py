import csv
import io
import json
import re
import shutil
import subprocess
import tempfile
import threading
import zipfile

from dataclasses import dataclass, field
from pathlib     import Path
from typing      import Any

from lxml import etree

from tools.web.web import HTMLTextExtractor


IMAGE_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"
})

TEXT_EXTENSIONS = frozenset({
    ".txt", ".md", ".markdown", ".rst", ".log", ".tex",
    ".json", ".jsonl", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".css", ".scss", ".sql",
    ".sh", ".bash", ".zsh", ".c", ".cpp", ".cc", ".h", ".hpp",
    ".rs", ".go", ".java", ".kt", ".rb", ".php", ".xml", ".svg",
})

CSV_EXTENSIONS = frozenset({".csv", ".tsv"})
HTML_EXTENSIONS = frozenset({".html", ".htm", ".xhtml"})
OPENDOC_EXTENSIONS = frozenset({".odt", ".ods", ".odp"})

SUPPORTED_EXTENSIONS = sorted(
    IMAGE_EXTENSIONS
    | TEXT_EXTENSIONS
    | CSV_EXTENSIONS
    | HTML_EXTENSIONS
    | OPENDOC_EXTENSIONS
    | {".pdf", ".docx", ".xlsx", ".xlsm", ".pptx", ".rtf", ".doc", ".xls", ".ppt", ".epub"}
)

DEFAULT_ATTACHMENT_MAX_CHARS = 16000

_ocr_lock = threading.Lock()
_ocr_engine = None


def _get_ocr():
    global _ocr_engine
    if _ocr_engine is None:
        with _ocr_lock:
            if _ocr_engine is None:
                from rapidocr_onnxruntime import RapidOCR
                _ocr_engine = RapidOCR()
    return _ocr_engine


def _group_ocr_boxes(result: list | None) -> tuple[str, int, float | None]:
    """Sort RapidOCR detections into natural top-to-bottom, left-to-right reading lines."""
    if not result:
        return "", 0, None

    entries = []
    confidences: list[float] = []
    for item in result:
        if not item or len(item) < 2:
            continue
        box, text = item[0], str(item[1]).strip()
        if not text:
            continue
        try:
            conf = float(item[2]) if len(item) > 2 else None
        except (TypeError, ValueError):
            conf = None
        if conf is not None:
            confidences.append(conf)

        xs = [pt[0] for pt in box]
        ys = [pt[1] for pt in box]
        y_center = (min(ys) + max(ys)) / 2.0
        height = max(1.0, max(ys) - min(ys))
        entries.append((y_center, min(xs), height, text))

    if not entries:
        return "", 0, None

    entries.sort(key=lambda e: (e[0], e[1]))
    lines: list[list[tuple[float, float, float, str]]] = []

    for entry in entries:
        y_c, _, height, _ = entry
        if not lines:
            lines.append([entry])
            continue
        prev_line = lines[-1]
        prev_y = sum(item[0] for item in prev_line) / len(prev_line)
        prev_h = max(item[2] for item in prev_line)
        tolerance = max(height, prev_h) * 0.55
        if abs(y_c - prev_y) <= tolerance:
            prev_line.append(entry)
        else:
            lines.append([entry])

    formatted_lines = []
    for line in lines:
        line.sort(key=lambda item: item[1])
        formatted_lines.append(" ".join(item[3] for item in line))

    avg_conf = round(sum(confidences) / len(confidences), 3) if confidences else None
    return "\n".join(formatted_lines), len(entries), avg_conf


def _clean_whitespace(text: str) -> str:
    lines = [line.rstrip() for line in (text or "").replace("\r\n", "\n").replace("\r", "\n").splitlines()]
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def format_attachment_prompt_block(attachments: list[dict] | None) -> str:
    """Format extracted attachment payloads into a clean context block for the LLM."""
    if not attachments:
        return ""

    blocks: list[str] = []
    for att in attachments:
        if not isinstance(att, dict):
            continue
        text = (att.get("text") or "").strip()
        if not text:
            continue
        filename = att.get("filename") or "attachment"
        fmt = att.get("format") or "file"
        method = att.get("method") or "extracted"
        meta = att.get("meta") or {}

        meta_parts = [f"Format: {fmt}", f"Extraction: {method}"]
        if isinstance(meta, dict):
            if meta.get("pages"):
                meta_parts.append(f"Pages: {meta['pages']}")
            if meta.get("width") and meta.get("height"):
                meta_parts.append(f"Dimensions: {meta['width']}x{meta['height']}")
            if att.get("truncated"):
                meta_parts.append("Status: truncated")

        header = (
            f"[ATTACHED FILE CONTENT — ALREADY EXTRACTED, DO NOT CALL read_file]\n"
            f"Filename: {filename} | {' | '.join(meta_parts)}\n"
            f"--- BEGIN EXTRACTED CONTENT ---"
        )
        blocks.append(f"{header}\n{text}\n--- END EXTRACTED CONTENT ---")

    return "\n\n".join(blocks)


class FileProcessingError(Exception):
    """Raised when reading or extracting text from a file fails."""
    pass


@dataclass
class ExtractedDocument:
    filename: str
    extension: str
    format: str
    method: str
    text: str
    char_count: int
    truncated: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "extension": self.extension,
            "format": self.format,
            "method": self.method,
            "text": self.text,
            "char_count": self.char_count,
            "truncated": self.truncated,
            "meta": self.meta,
        }


class FileProcessor:
    """Unified extractor supporting text, markdown, office docs, legacy formats, PDFs, and OCR."""

    MAX_PDF_OCR_PAGES = 20
    MAX_PDF_TEXT_PAGES = 100
    MAX_SPREADSHEET_ROWS = 500

    @staticmethod
    def _decode_text(data: bytes) -> str:
        for encoding in ("utf-8-sig", "utf-8", "utf-16"):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue
        return data.decode("latin-1", errors="replace")

    @classmethod
    def _ocr_image_bytes(cls, data: bytes) -> tuple[str, dict[str, Any]]:
        import numpy as np
        from PIL import Image, ImageOps

        with Image.open(io.BytesIO(data)) as raw_img:
            img = ImageOps.exif_transpose(raw_img)
            width, height = img.size
            orig_format = (raw_img.format or "image").lower()
            if img.mode != "RGB":
                img = img.convert("RGB")
            arr = np.array(img)

        ocr = _get_ocr()
        result, _ = ocr(arr)
        text, line_count, avg_conf = _group_ocr_boxes(result)
        meta: dict[str, Any] = {
            "width": width,
            "height": height,
            "image_format": orig_format,
            "ocr_blocks": line_count,
        }
        if avg_conf is not None:
            meta["ocr_confidence"] = avg_conf

        if not text:
            text = "[No readable text detected by OCR in image.]"
        return text, meta

    @classmethod
    def _extract_html(cls, data: bytes) -> tuple[str, dict[str, Any]]:
        extractor = HTMLTextExtractor()
        extractor.feed(cls._decode_text(data))
        extractor.close()
        title, body = extractor.get_result()
        text = f"Title: {title}\n\n{body}" if title and body else (body or title)
        return text, {"title": title} if title else {}

    @classmethod
    def _extract_csv(cls, data: bytes, delimiter: str = ",") -> tuple[str, dict[str, Any]]:
        decoded = cls._decode_text(data)
        reader = csv.reader(io.StringIO(decoded), delimiter=delimiter)
        rows = []
        total_rows = 0
        for row in reader:
            total_rows += 1
            if len(rows) < cls.MAX_SPREADSHEET_ROWS:
                cells = [cell.replace("\n", " ").strip() for cell in row]
                rows.append(" | ".join(cells))
        text = "\n".join(rows)
        return text, {"rows": total_rows, "truncated_rows": total_rows > cls.MAX_SPREADSHEET_ROWS}

    @classmethod
    def _extract_docx(cls, data: bytes) -> tuple[str, dict[str, Any]]:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        doc = Document(io.BytesIO(data))
        blocks: list[str] = []

        for child in doc.element.body.iterchildren():
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "p":
                para = Paragraph(child, doc)
                text = para.text.strip()
                if not text:
                    continue
                style_name = (para.style.name or "").lower() if para.style else ""
                if style_name.startswith("heading"):
                    level_match = re.search(r"(\d+)", style_name)
                    level = min(6, int(level_match.group(1))) if level_match else 2
                    blocks.append(f"{'#' * level} {text}")
                else:
                    blocks.append(text)
            elif tag == "tbl":
                table = Table(child, doc)
                table_lines = []
                for row in table.rows:
                    cells = [" ".join(cell.text.split()) for cell in row.cells]
                    if any(cells):
                        table_lines.append(" | ".join(cells))
                if table_lines:
                    blocks.append("\n".join(table_lines))

        return "\n\n".join(blocks), {"paragraphs": len(doc.paragraphs), "tables": len(doc.tables)}

    @classmethod
    def _extract_xlsx(cls, data: bytes) -> tuple[str, dict[str, Any]]:
        import openpyxl

        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        sections: list[str] = []
        try:
            for sheet_name in wb.sheetnames:
                ws = wb[sheet_name]
                rows_out: list[str] = []
                row_count = 0
                for row in ws.iter_rows(values_only=True):
                    if not any(v is not None and str(v).strip() != "" for v in row):
                        continue
                    row_count += 1
                    if len(rows_out) < cls.MAX_SPREADSHEET_ROWS:
                        cells = ["" if v is None else str(v).replace("\n", " ").strip() for v in row]
                        rows_out.append(" | ".join(cells))
                if rows_out:
                    sections.append(f"## Sheet: {sheet_name}\n" + "\n".join(rows_out))
            return "\n\n".join(sections), {"sheets": list(wb.sheetnames)}
        finally:
            wb.close()

    @classmethod
    def _extract_xls(cls, data: bytes) -> tuple[str, dict[str, Any]]:
        import xlrd

        wb = xlrd.open_workbook(file_contents=data)
        sections: list[str] = []
        for sheet in wb.sheets():
            rows_out: list[str] = []
            for r in range(min(sheet.nrows, cls.MAX_SPREADSHEET_ROWS)):
                vals = [str(sheet.cell_value(r, c)).replace("\n", " ").strip() for c in range(sheet.ncols)]
                if any(vals):
                    rows_out.append(" | ".join(vals))
            if rows_out:
                sections.append(f"## Sheet: {sheet.name}\n" + "\n".join(rows_out))
        return "\n\n".join(sections), {"sheets": wb.sheet_names()}

    @classmethod
    def _extract_pptx(cls, data: bytes) -> tuple[str, dict[str, Any]]:
        from pptx import Presentation

        prs = Presentation(io.BytesIO(data))
        slides_out: list[str] = []
        for idx, slide in enumerate(prs.slides, start=1):
            parts: list[str] = []
            for shape in slide.shapes:
                if shape.has_text_frame:
                    txt = "\n".join(p.text.strip() for p in shape.text_frame.paragraphs if p.text.strip())
                    if txt:
                        parts.append(txt)
                if shape.has_table:
                    for row in shape.table.rows:
                        cells = [" ".join(cell.text.split()) for cell in row.cells]
                        if any(cells):
                            parts.append(" | ".join(cells))
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
                notes = slide.notes_slide.notes_text_frame.text.strip()
                if notes:
                    parts.append(f"[Speaker Notes]: {notes}")
            if parts:
                slides_out.append(f"## Slide {idx}\n" + "\n".join(parts))

        return "\n\n".join(slides_out), {"slides": len(prs.slides)}

    @classmethod
    def _extract_opendocument(cls, data: bytes) -> tuple[str, dict[str, Any]]:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            if "content.xml" not in zf.namelist():
                raise FileProcessingError("Invalid OpenDocument archive (missing content.xml).")
            xml_bytes = zf.read("content.xml")

        root = etree.fromstring(xml_bytes)
        lines: list[str] = []
        for elem in root.iter():
            local = elem.tag.rsplit("}", 1)[-1]
            if local in ("h", "p"):
                txt = "".join(elem.itertext()).strip()
                if txt:
                    if local == "h":
                        lines.append(f"## {txt}")
                    else:
                        lines.append(txt)
        return "\n\n".join(lines), {"format": "opendocument"}

    @classmethod
    def _extract_epub(cls, data: bytes) -> tuple[str, dict[str, Any]]:
        chapters: list[str] = []
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            html_names = sorted(
                name for name in zf.namelist()
                if name.lower().endswith((".xhtml", ".html", ".htm"))
            )
            for name in html_names:
                text, _ = cls._extract_html(zf.read(name))
                if text:
                    chapters.append(text)
        return "\n\n".join(chapters), {"chapters": len(chapters)}

    @classmethod
    def _extract_pdf(
        cls,
        data: bytes,
        page_start: int | None = None,
        page_end: int | None = None,
    ) -> tuple[str, str, dict[str, Any]]:
        from pypdf import PdfReader

        ocr_pages = 0
        text_pages = 0
        chunks: list[str] = []

        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "doc.pdf"
            pdf_path.write_bytes(data)

            try:
                reader = PdfReader(io.BytesIO(data))
                total_pages = len(reader.pages)
            except Exception as exc:
                # Fallback to pdftotext if pypdf cannot parse PDF
                if shutil.which("pdftotext"):
                    proc = subprocess.run(
                        ["pdftotext", "-layout", str(pdf_path), "-"],
                        capture_output=True, text=True, timeout=30
                    )
                    if proc.returncode == 0 and proc.stdout.strip():
                        return _clean_whitespace(proc.stdout), "pdftotext", {"pages": None}
                raise FileProcessingError(f"Failed to read PDF: {exc}") from exc

            start_idx = max(1, page_start or 1)
            end_idx = min(total_pages, page_end or min(total_pages, start_idx + cls.MAX_PDF_TEXT_PAGES - 1))

            pdftoppm_bin = shutil.which("pdftoppm")

            for p_num in range(start_idx, end_idx + 1):
                page_text = ""
                try:
                    page_text = (reader.pages[p_num - 1].extract_text() or "").strip()
                except Exception:
                    page_text = ""

                # If embedded text is missing or negligible, treat page as scanned and OCR it
                if len(re.sub(r"\s+", "", page_text)) < 25 and pdftoppm_bin and ocr_pages < cls.MAX_PDF_OCR_PAGES:
                    prefix = Path(tmpdir) / f"page_{p_num}"
                    proc = subprocess.run(
                        [pdftoppm_bin, "-png", "-r", "180", "-f", str(p_num), "-l", str(p_num), str(pdf_path), str(prefix)],
                        capture_output=True, text=True, timeout=30
                    )
                    rendered = sorted(Path(tmpdir).glob(f"page_{p_num}*.png"))
                    if proc.returncode == 0 and rendered:
                        ocr_text, _ = cls._ocr_image_bytes(rendered[0].read_bytes())
                        if ocr_text and not ocr_text.startswith("[No readable text"):
                            ocr_pages += 1
                            chunks.append(f"## Page {p_num} (OCR)\n{ocr_text}")
                            continue

                if page_text:
                    text_pages += 1
                    chunks.append(f"## Page {p_num}\n{page_text}")

        method = "hybrid-ocr" if (ocr_pages and text_pages) else ("ocr" if ocr_pages else "pdf-text")
        meta = {
            "pages": total_pages,
            "pages_processed": f"{start_idx}-{end_idx}" if total_pages else "0",
            "text_pages": text_pages,
            "ocr_pages": ocr_pages,
        }
        return "\n\n".join(chunks), method, meta

    @classmethod
    def _extract_legacy_binary_doc_or_ppt(cls, data: bytes, ext: str) -> tuple[str, str, dict[str, Any]]:
        # 1. Check if actually misnamed RTF / HTML / OOXML ZIP
        head = data[:64].lstrip()
        if head.startswith(b"{\\rtf"):
            from striprtf.striprtf import rtf_to_text
            return _clean_whitespace(rtf_to_text(cls._decode_text(data))), "rtf", {"misnamed": ext}
        if head.lower().startswith((b"<!doctype html", b"<html", b"<head", b"<body")):
            text, meta = cls._extract_html(data)
            return text, "html", {**meta, "misnamed": ext}
        if data[:4] == b"PK\x03\x04":
            if ext == ".doc":
                text, meta = cls._extract_docx(data)
                return text, "docx", {**meta, "misnamed": ext}
            if ext == ".ppt":
                text, meta = cls._extract_pptx(data)
                return text, "pptx", {**meta, "misnamed": ext}

        # 2. Try external CLI converters if installed
        with tempfile.TemporaryDirectory() as tmpdir:
            in_path = Path(tmpdir) / f"input{ext}"
            in_path.write_bytes(data)

            if ext == ".doc":
                for tool in ("antiword", "catdoc"):
                    bin_path = shutil.which(tool)
                    if bin_path:
                        proc = subprocess.run([bin_path, str(in_path)], capture_output=True, text=True, timeout=20)
                        if proc.returncode == 0 and proc.stdout.strip():
                            return _clean_whitespace(proc.stdout), tool, {"legacy": ext}

            soffice = shutil.which("soffice") or shutil.which("libreoffice")
            if soffice:
                proc = subprocess.run(
                    [soffice, "--headless", "--convert-to", "txt:Text", "--outdir", tmpdir, str(in_path)],
                    capture_output=True, text=True, timeout=30
                )
                out_txt = Path(tmpdir) / "input.txt"
                if proc.returncode == 0 and out_txt.is_file():
                    return _clean_whitespace(out_txt.read_text(encoding="utf-8", errors="replace")), "libreoffice", {"legacy": ext}

        # 3. Pure-Python OLE2 fallback (olefile)
        import olefile

        if not olefile.isOleFile(io.BytesIO(data)):
            # Plain text disguised as .doc
            decoded = cls._decode_text(data)
            printable = sum(1 for c in decoded if c.isprintable() or c in "\r\n\t")
            if decoded and (printable / len(decoded)) > 0.85:
                return _clean_whitespace(decoded), "plain-text", {"legacy": ext}
            raise FileProcessingError(f"Unrecognized or corrupted {ext} binary format.")

        extracted_runs: list[str] = []
        with olefile.OleFileIO(io.BytesIO(data)) as ole:
            candidate_streams = (
                ("WordDocument", "1Table", "0Table")
                if ext == ".doc"
                else ("PowerPoint Document",)
            )
            streams_to_scan = [
                name for name in candidate_streams if ole.exists(name)
            ]
            if not streams_to_scan:
                streams_to_scan = [
                    "/".join(entry)
                    for entry in ole.listdir()
                    if not entry[0].startswith("\x05")
                ]

            for stream_name in streams_to_scan:
                raw = ole.openstream(stream_name).read()
                # Extract readable UTF-16LE sequences first, then ASCII sequences
                utf16_matches = re.findall(rb"(?:[\x20-\x7e\r\n\t]\x00){8,}", raw)
                for m in utf16_matches:
                    s = m.decode("utf-16le", errors="ignore").strip()
                    if len(s) >= 8:
                        extracted_runs.append(s)
                if not utf16_matches:
                    ascii_matches = re.findall(rb"[\x20-\x7e\r\n\t]{12,}", raw)
                    for m in ascii_matches:
                        s = m.decode("cp1252", errors="ignore").strip()
                        if len(s) >= 12 and not s.startswith(("Microsoft ", "Root Entry", "Word.Document")):
                            extracted_runs.append(s)

        # Deduplicate preserving order
        seen: set[str] = set()
        unique_runs = []
        for run in extracted_runs:
            cleaned = _clean_whitespace(run)
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                unique_runs.append(cleaned)

        return "\n\n".join(unique_runs), "ole2-fallback", {"legacy": ext}

    @classmethod
    def extract_bytes(
        cls,
        data: bytes,
        filename: str,
        max_chars: int = DEFAULT_ATTACHMENT_MAX_CHARS,
        page_start: int | None = None,
        page_end: int | None = None,
    ) -> ExtractedDocument:
        if not data:
            raise FileProcessingError(f"File '{filename}' is empty.")

        ext = Path(filename).suffix.lower()
        fmt = ext.lstrip(".") or "text"
        method = "text"
        meta: dict[str, Any] = {"bytes": len(data)}

        if ext in IMAGE_EXTENSIONS:
            fmt = "image"
            method = "ocr"
            text, extra_meta = cls._ocr_image_bytes(data)
            meta.update(extra_meta)
        elif ext == ".pdf":
            fmt = "pdf"
            text, method, extra_meta = cls._extract_pdf(data, page_start=page_start, page_end=page_end)
            meta.update(extra_meta)
        elif ext == ".docx":
            fmt = "docx"
            method = "python-docx"
            text, extra_meta = cls._extract_docx(data)
            meta.update(extra_meta)
        elif ext in (".xlsx", ".xlsm"):
            fmt = "xlsx"
            method = "openpyxl"
            text, extra_meta = cls._extract_xlsx(data)
            meta.update(extra_meta)
        elif ext == ".xls":
            fmt = "xls"
            method = "xlrd"
            text, extra_meta = cls._extract_xls(data)
            meta.update(extra_meta)
        elif ext == ".pptx":
            fmt = "pptx"
            method = "python-pptx"
            text, extra_meta = cls._extract_pptx(data)
            meta.update(extra_meta)
        elif ext == ".rtf":
            from striprtf.striprtf import rtf_to_text
            fmt = "rtf"
            method = "striprtf"
            text = rtf_to_text(cls._decode_text(data))
        elif ext in OPENDOC_EXTENSIONS:
            fmt = ext.lstrip(".")
            method = "opendocument-xml"
            text, extra_meta = cls._extract_opendocument(data)
            meta.update(extra_meta)
        elif ext == ".epub":
            fmt = "epub"
            method = "epub-html"
            text, extra_meta = cls._extract_epub(data)
            meta.update(extra_meta)
        elif ext in (".doc", ".ppt"):
            fmt = ext.lstrip(".")
            text, method, extra_meta = cls._extract_legacy_binary_doc_or_ppt(data, ext)
            meta.update(extra_meta)
        elif ext in CSV_EXTENSIONS:
            fmt = ext.lstrip(".")
            method = "csv"
            text, extra_meta = cls._extract_csv(data, delimiter="\t" if ext == ".tsv" else ",")
            meta.update(extra_meta)
        elif ext in HTML_EXTENSIONS:
            fmt = "html"
            method = "html-parser"
            text, extra_meta = cls._extract_html(data)
            meta.update(extra_meta)
        elif ext in TEXT_EXTENSIONS or not ext:
            fmt = "markdown" if ext in (".md", ".markdown") else (ext.lstrip(".") or "text")
            method = "text"
            raw = cls._decode_text(data)
            if ext == ".json":
                try:
                    raw = json.dumps(json.loads(raw), indent=2, ensure_ascii=False)
                except json.JSONDecodeError:
                    pass
            text = raw
        else:
            # Best-effort check for arbitrary text-based files (e.g. .gitignore, Makefile, .astro)
            if b"\x00" in data[:4096]:
                raise FileProcessingError(
                    f"Unsupported binary format '{ext or filename}'. Supported: {', '.join(SUPPORTED_EXTENSIONS)}"
                )
            fmt = ext.lstrip(".") or "text"
            method = "text"
            text = cls._decode_text(data)

        cleaned = _clean_whitespace(text)
        if not cleaned:
            cleaned = f"[No readable text extracted from {filename}.]"

        truncated = False
        if max_chars and len(cleaned) > max_chars:
            cleaned = cleaned[:max_chars - 32].rstrip() + "\n\n[Truncated]"
            truncated = True

        return ExtractedDocument(
            filename=Path(filename).name,
            extension=ext,
            format=fmt,
            method=method,
            text=cleaned,
            char_count=len(cleaned),
            truncated=truncated,
            meta=meta,
        )

    @classmethod
    def extract_path(
        cls,
        path: str | Path,
        max_chars: int = DEFAULT_ATTACHMENT_MAX_CHARS,
        page_start: int | None = None,
        page_end: int | None = None,
    ) -> ExtractedDocument:
        target = Path(path).expanduser()
        if not target.is_file():
            raise FileProcessingError(f"File not found: {path}")
        return cls.extract_bytes(
            target.read_bytes(),
            target.name,
            max_chars=max_chars,
            page_start=page_start,
            page_end=page_end,
        )
