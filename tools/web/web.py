import gzip
import json
import re
import zlib

from html.parser   import HTMLParser
from urllib.error  import HTTPError, URLError
from urllib.parse  import urlsplit
from urllib.request import Request, urlopen

from ddgs import DDGS

from core.tool_manager import ToolManager


_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class HTMLTextExtractor(HTMLParser):
    """Per-request HTML-to-readable-text extractor."""

    IGNORE_TAGS = frozenset({
        "script", "style", "noscript", "svg", "template", "iframe", "canvas"
    })
    NOISE_TAGS = frozenset({"nav", "footer", "aside"})
    BLOCK_TAGS = frozenset({
        "p", "div", "section", "article", "main", "header",
        "h1", "h2", "h3", "h4", "h5", "h6",
        "li", "tr", "blockquote", "pre", "dt", "dd", "figcaption", "hr"
    })

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ignore_depth = 0
        self.head_depth = 0
        self.noise_depth = 0
        self.pre_depth = 0
        self.in_title = False

        self.title_parts: list[str] = []
        self.main_parts: list[str] = []
        self.all_parts: list[str] = []

    def _emit(self, chunk: str, main_only: bool = False):
        if not chunk:
            return
        self.all_parts.append(chunk)
        if not main_only or self.noise_depth == 0:
            self.main_parts.append(chunk)

    def handle_starttag(self, tag: str, attrs):
        tag = tag.lower()
        if tag in self.IGNORE_TAGS:
            self.ignore_depth += 1
            return
        if tag == "head":
            self.head_depth += 1
            return
        if tag == "title" and self.ignore_depth == 0:
            self.in_title = True
            return
        if self.ignore_depth > 0 or self.head_depth > 0:
            return

        if tag in self.NOISE_TAGS:
            self.noise_depth += 1
        if tag == "pre":
            self.pre_depth += 1
            self._emit("\n", main_only=True)
        elif tag == "br":
            self._emit("\n", main_only=True)
        elif tag == "li":
            self._emit("\n- ", main_only=True)
        elif tag in ("td", "th"):
            self._emit(" ", main_only=True)
        elif tag in self.BLOCK_TAGS:
            self._emit("\n", main_only=True)

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        if tag in self.IGNORE_TAGS:
            if self.ignore_depth > 0:
                self.ignore_depth -= 1
            return
        if tag == "head":
            if self.head_depth > 0:
                self.head_depth -= 1
            return
        if tag == "title":
            self.in_title = False
            return
        if self.ignore_depth > 0 or self.head_depth > 0:
            return

        if tag == "pre" and self.pre_depth > 0:
            self.pre_depth -= 1
            self._emit("\n", main_only=True)
        elif tag in self.BLOCK_TAGS:
            self._emit("\n", main_only=True)
        if tag in self.NOISE_TAGS and self.noise_depth > 0:
            self.noise_depth -= 1

    def handle_data(self, data: str):
        if self.in_title:
            cleaned = re.sub(r"\s+", " ", data)
            if cleaned:
                self.title_parts.append(cleaned)
            return
        if self.ignore_depth > 0 or self.head_depth > 0:
            return
        if self.pre_depth > 0:
            self._emit(data, main_only=True)
            return

        collapsed = re.sub(r"\s+", " ", data)
        if collapsed:
            self._emit(collapsed, main_only=True)

    @staticmethod
    def _clean_text(parts: list[str]) -> str:
        raw = "".join(parts)
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in raw.splitlines()]
        text = "\n".join(lines)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def get_result(self) -> tuple[str, str]:
        title = re.sub(r"\s+", " ", "".join(self.title_parts)).strip()
        main_text = self._clean_text(self.main_parts)
        if len(main_text) >= 200:
            return title, main_text
        all_text = self._clean_text(self.all_parts)
        return title, (main_text if len(main_text) >= len(all_text) else all_text)


class WebFunctions:
    MAX_RESULTS = 5
    MAX_FETCH_BYTES = 1_000_000
    MAX_FETCH_CHARS = 8000

    def web_search(self, query: str):
        if not query.strip():
            return "Search query cannot be empty."
        try:
            res = DDGS(timeout=20).text(
                query,
                region="in-en",
                safesearch="off",
                max_results=self.MAX_RESULTS
            )
        except Exception as exc:
            return f"Web search failed: {type(exc).__name__}: {exc}"
        if not res:
            return "The search provider returned zero results."
        return "\n\n".join(
            f"Title: {r['title']}\n"
            f"URL: {r['href']}\n"
            f"Snippet: {r['body']}"
            for r in res
        )

    @staticmethod
    def _decode_body(raw_bytes: bytes, content_encoding: str, charset: str) -> str:
        encoding = (content_encoding or "").strip().lower()
        data = raw_bytes
        if "gzip" in encoding:
            try:
                data = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(raw_bytes)
            except zlib.error:
                data = gzip.decompress(raw_bytes)
        elif "deflate" in encoding:
            try:
                data = zlib.decompressobj(zlib.MAX_WBITS).decompress(raw_bytes)
            except zlib.error:
                data = zlib.decompressobj(-zlib.MAX_WBITS).decompress(raw_bytes)

        try:
            return data.decode(charset or "utf-8", errors="replace")
        except LookupError:
            return data.decode("utf-8", errors="replace")

    def web_fetch(self, url: str) -> str:
        clean_url = (url or "").strip()
        if not clean_url:
            return "Fetch failed: URL cannot be empty."

        parsed = urlsplit(clean_url)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
            return "Fetch failed: URL must start with http:// or https:// and include a valid host."

        request = Request(
            clean_url,
            headers={
                "User-Agent": _DEFAULT_USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/json,text/plain;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate",
            }
        )

        try:
            with urlopen(request, timeout=15) as res:
                final_url = res.geturl() or clean_url
                content_type = (res.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                charset = res.headers.get_content_charset() or "utf-8"
                content_encoding = res.headers.get("Content-Encoding") or ""

                raw_bytes = res.read(self.MAX_FETCH_BYTES + 1)
                bytes_truncated = len(raw_bytes) > self.MAX_FETCH_BYTES
                if bytes_truncated:
                    raw_bytes = raw_bytes[:self.MAX_FETCH_BYTES]

                text_body = self._decode_body(raw_bytes, content_encoding, charset)

        except HTTPError as exc:
            return f"Fetch failed: HTTP {exc.code} {exc.reason}".strip()
        except URLError as exc:
            return f"Fetch failed: {exc.reason}"
        except Exception as exc:
            return f"Fetch failed: {type(exc).__name__}: {exc}"

        title = ""
        if not content_type or content_type in ("text/html", "application/xhtml+xml"):
            extractor = HTMLTextExtractor()
            extractor.feed(text_body)
            extractor.close()
            title, body = extractor.get_result()
        elif content_type == "application/json" or content_type.endswith("+json"):
            try:
                body = json.dumps(json.loads(text_body), indent=2, ensure_ascii=False)
            except json.JSONDecodeError:
                body = text_body.strip()
        elif content_type.startswith("text/") or content_type in ("application/xml", "text/xml"):
            body = re.sub(r"\n{3,}", "\n\n", text_body).strip()
        else:
            return f"Unsupported content type: {content_type}"

        if not body:
            return f"No readable text content found at {final_url}."

        header_lines = []
        if title:
            header_lines.append(f"Title: {title}")
        header_lines.append(f"URL: {final_url}")
        output = "\n".join(header_lines) + "\n\n" + body

        if len(output) > self.MAX_FETCH_CHARS:
            output = output[:self.MAX_FETCH_CHARS - 45].rstrip() + "\n\n[Truncated: page exceeds fetch limit]"
        elif bytes_truncated:
            output += "\n\n[Truncated: response exceeded 1 MB download limit]"

        return output


_wF = WebFunctions()


def register(tool_manager: ToolManager):
    tool_manager.register(
        name="web_search",
        description=(
            "Search the web for real-time information, news, holidays, festivals, and external facts. "
            "Always convert relative dates (e.g., 'yesterday', 'tomorrow') into absolute calendar dates "
            "(e.g., 'November 8 2026') and include regional context (e.g., 'India') when applicable."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Specific search terms. Use absolute dates (e.g., 'November 8 2026') rather than "
                        "relative phrases like 'yesterday' or 'today'."
                    )
                }
            },
            "required": ["query"],
            "additionalProperties": False
        },
        handler=_wF.web_search
    )

    tool_manager.register(
        name="web_fetch",
        description=(
            "Fetch and extract readable text from a webpage, plain text file, or JSON URL. "
            "Use this to inspect full details from a specific URL returned by web_search or provided by the user."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "HTTP or HTTPS URL to fetch."
                }
            },
            "required": ["url"],
            "additionalProperties": False
        },
        handler=_wF.web_fetch
    )
