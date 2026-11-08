from core.tool_manager import ToolManager

from urllib.request import Request, urlopen
from html.parser    import HTMLParser

from ddgs import DDGS

class Extractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text = []
        self.ignore = False
        
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.ignore = True
            
    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self.ignore = False
            
    def handle_data(self, data):
        if not self.ignore:
            self.text.append(data.strip())
            
_parser = Extractor()

class WebFunctions:
    MAX_RESULTS = 5
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
        
    def web_fetch(self, url: str) -> str:
        """NEED MAJOR REFACTOR"""
        try:
            request = Request(
                url,
                headers={"User-Agent": "PAIN/1.0"}
            )
            with urlopen(request, timeout=15) as res:
                content_type = res.headers.get("Content-Type", "")
                if "text/html" not in content_type and "text/plain" not in content_type:
                    return "Unsupported content type."
                html = res.read(500000).decode("utf-8", errors="replace")

                _parser.feed(html)
                return "\n".join(t for t in _parser.text if t)[:12000]
        except Exception as exc:
            return f"Fetch failed: {exc}"
        

_wF = WebFunctions()

def register(tool_manager: ToolManager):
    tool_manager.register(
        name = "web_search",
        description = ("Search the web for information and recent developments."),
        parameters = {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Search query."
                    )
                }
            },
            "required": ["query"],
            "additionalProperties": False
        },
        handler= _wF.web_search
    )
    
    tool_manager.register(
        name="web_fetch",
        description="Fetch and extract readable text from a webpage.",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to fetch"}
            },
            "required": ["url"],
            "additionalProperties": False
        },
        handler=_wF.web_fetch
    )