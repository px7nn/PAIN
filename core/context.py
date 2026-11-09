import json
import math
import re
from datetime import datetime

from dataclasses    import dataclass
from urllib.request import Request, urlopen

from core.config import Config, config
from core.conversation import ConversationManager


class ContextError(Exception):
    """Raised when context construction fails"""
    ...


@dataclass
class ContextWindow:
    model: str
    messages: list[dict]

    token_estimate: int
    context_budget: int

    omitted_messages: int
    tokenizer: str


class ContextManager:
    def __init__(
        self,
        conversations: ConversationManager,
        cfg: Config = config,
        models=None,
        safety_margin: int = 256,
        token_counter=None
    ):
        self.config        = cfg
        self.conversations = conversations
        self.models        = models
        self.safety_margin = safety_margin
        self.token_counter = token_counter

        # In-memory cache for token counts and normalized personalities
        self._token_cache       : dict[str, int] = {}
        self._personality_cache : dict[str, str] = {}

    # --------------------------------------------------
    # PERSONALITY NORMALIZATION (KV-CACHE FRIENDLY)
    # --------------------------------------------------

    @staticmethod
    def _normalize_personality_text(raw_text: str) -> str:
        """Strip decorative ASCII lines and wasteful whitespace to compress tokens"""
        lines = []
        for line in raw_text.splitlines():
            # Remove purely decorative divider lines like ──────── or ════════
            if re.match(r"^\s*[─═\-_=*#~]{3,}\s*$", line):
                continue
            lines.append(line.rstrip())

        cleaned = "\n".join(lines)
        # Collapse 3+ consecutive newlines down to 2
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    def load_personality(self, model_name: str) -> str:
        """Load and normalize the static personality prompt."""
        if model_name in self._personality_cache:
            return self._personality_cache[model_name]

        model = self.config.get_model(model_name)
        path = model.personality_path
        if not path.is_file():
            raise ContextError(f"Personality file not found: {path}")

        try:
            raw_content = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ContextError(f"Unable to read personality: {exc}") from exc

        if not raw_content:
            raise ContextError(f"Personality file is empty: {path}")

        normalized = self._normalize_personality_text(raw_content)
        self._personality_cache[model_name] = normalized
        return normalized

    # --------------------------------------------------
    # TOKEN ESTIMATION (EXACT OR CALIBRATED HEURISTIC)
    # --------------------------------------------------

    def estimate_tokens(self, text: str) -> int:
        """Estimate token count using native llama-server tokenize or calibrated fallback"""
        if not text:
            return 0

        if self.token_counter is not None:
            count = self.token_counter(text)
            if isinstance(count, int) and count >= 0:
                return count

        if text in self._token_cache:
            return self._token_cache[text]

        # Try llama-server /tokenize endpoint if models manager is active
        if self.models and hasattr(self.models, "base_url") and self.models.is_running:
            try:
                body = json.dumps({"content": text}).encode("utf-8")
                req = Request(
                    f"{self.models.base_url}/tokenize",
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urlopen(req, timeout=1.0) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                    tokens = result.get("tokens")
                    if isinstance(tokens, list):
                        count = len(tokens)
                        if len(self._token_cache) < 2500:
                            self._token_cache[text] = count
                        return count
            except Exception:
                pass

        # combine word and character densities
        words = len(text.split())
        chars = len(text)
        # Average of ~1.3 tokens per word and ~3.8 characters per token
        count = max(1, math.ceil((words * 1.3 + chars / 3.8) / 2))

        if len(self._token_cache) < 2500:
            self._token_cache[text] = count
        return count

    def _message_tokens(self, message: dict) -> int:
        content = message.get("content") or ""
        # 4 tokens overhead for chatML/template wrappers (<|im_start|>role\n...<|im_end|>)
        return self.estimate_tokens(content) + 4

    def _tool_tokens(self, tools: list[dict] | None) -> int:
        if not tools:
            return 0
        try:
            return self.estimate_tokens(json.dumps(tools, ensure_ascii=False)) + 16
        except Exception:
            return 250

    # --------------------------------------------------
    # CONTEXT BUDGETING
    # --------------------------------------------------

    def _get_budget(self, model_name: str) -> tuple[int, int]:
        model = self.config.get_model(model_name)
        context_size = model.context_size
        max_tokens = self.config.inference.max_tokens

        budget = context_size - max_tokens - self.safety_margin

        if budget <= 0:
            raise ContextError(
                f"Insufficient context capacity. Context: {context_size}, "
                f"Output reserve: {max_tokens}, Safety margin: {self.safety_margin}"
            )

        return context_size, budget

    # --------------------------------------------------
    # ROLLING CONVERSATION COMPACTOR
    # --------------------------------------------------

    def _compact_older_messages(self, messages: list[dict]) -> str:
        """Compress older turns into a high-density reference brief"""
        if not messages:
            return ""

        summaries = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "").strip()
            if not content:
                continue

            # Take the primary takeaway/intent from the turn
            lines = content.splitlines()
            first_line = lines[0] if lines else content
            if len(first_line) > 120:
                first_line = first_line[:117] + "..."

            if role == "user":
                summaries.append(f"• User requested: {first_line}")
            elif role == "assistant":
                summaries.append(f"  PAIN response: {first_line}")

        return "\n".join(summaries[-10:])  # Cap summary at last 10 points

    # --------------------------------------------------
    # CONTEXT CONSTRUCTION
    # --------------------------------------------------

    def build(
        self,
        conversation_id: int,
        model_name: str,
        memory_context: str | None = None,
        tools: list[dict] | None = None
    ) -> ContextWindow:
        model = self.config.get_model(model_name)

        # Verify conversation
        conversation = self.conversations.get_conversation(conversation_id)
        if conversation is None:
            raise ContextError(f"Conversation not found: {conversation_id}")

        context_size, budget = self._get_budget(model_name)

        # --- Immutable Static Root System Prompt ---
        
        # Pure static persona text with NO volatile memories appended to guarantee KV-cache reuse
        system_content = self.load_personality(model_name)
        root_system_message = {
            "role": "system",
            "content": system_content
        }
        system_tokens = self._message_tokens(root_system_message)

        if system_tokens >= budget:
            raise ContextError("Personality exceeds the available context budget.")

        # Real-time environment and calendar context (stable by day to maximize KV-cache reuse)
        now = datetime.now().astimezone()
        env_message = {
            "role": "system",
            "content": (
                "[System Environment & Real-Time Context]\n"
                f"Current Date: {now.strftime('%A, %B %d, %Y')}\n"
                f"Local Timezone: {now.tzname() or 'Local'} (UTC{now.strftime('%z')})"
            )
        }
        env_tokens = self._message_tokens(env_message)

        # Dynamic Tool Schemas Reservation
        tool_tokens = self._tool_tokens(tools)

        # Gated Memory Reference Block (Separated from root persona)
        memory_message = None
        memory_tokens = 0
        if memory_context and memory_context.strip():
            # Cap memory block to ~250 tokens
            clean_mem = memory_context.strip()
            memory_message = {
                "role": "system",
                "content": (
                    "[Stored Memory & Context]\n"
                    "Background reference facts retrieved from past interactions. "
                    "Current user instructions take precedence:\n"
                    f"{clean_mem}"
                )
            }
            memory_tokens = self._message_tokens(memory_message)

        # Retrieve conversation history (excluding system messages)
        raw_history = self.conversations.get_messages(conversation_id)
        history = [
            {"role": m["role"], "content": m["content"]}
            for m in raw_history
            if m["role"] != "system"
        ]

        # --- Allocate Budget for Verbatim Turns vs Rolling Summary ---
        
        # Reserve headroom for multi-round agent tool calls (e.g. 600 tokens)
        agent_working_headroom = 600 if tools else 0
        static_overhead = system_tokens + env_tokens + tool_tokens + memory_tokens + agent_working_headroom

        available_for_history = budget - static_overhead
        if available_for_history < 400:
            # Squeeze agent headroom if context is very constrained
            available_for_history = budget - (system_tokens + tool_tokens + memory_tokens)

        # Select recent verbatim messages backwards
        selected_recent: list[dict] = []
        used_history_tokens = 0
        omitted_messages: list[dict] = []

        for idx, message in enumerate(reversed(history)):
            msg_tokens = self._message_tokens(message)

            # Always preserve at least the latest user message
            if not selected_recent:
                selected_recent.append(message)
                used_history_tokens += msg_tokens
                continue

            # Keep adding recent turns if within history budget
            if used_history_tokens + msg_tokens <= available_for_history:
                selected_recent.append(message)
                used_history_tokens += msg_tokens
            else:
                # All older turns that exceed the verbatim window are tracked for compaction
                remaining_old = list(reversed(history))[idx:]
                omitted_messages = list(reversed(remaining_old))
                break

        selected_recent.reverse()

        # Rolling Conversation Summary for Compacted Messages
        summary_message = None
        summary_tokens = 0

        if omitted_messages:
            # Check if an existing stored summary exists
            stored_summary = self.conversations.get_summary(conversation_id)
            if not stored_summary:
                stored_summary = self._compact_older_messages(omitted_messages)
                try:
                    self.conversations.update_summary(conversation_id, stored_summary)
                except Exception:
                    pass

            if stored_summary:
                summary_message = {
                    "role": "system",
                    "content": f"[Earlier Conversation Summary]\n{stored_summary}"
                }
                summary_tokens = self._message_tokens(summary_message)

        # Assemble Final Ordered Context Window
        # Ordering preserves KV cache:
        # [0]: Immutable root personality (100% static)
        # [1]: Real-time environment and calendar context (stable by day)
        # [2]: Stored reference memory (if any)
        # [3]: Earlier conversation summary (if any)
        # [4..N]: Recent verbatim user and assistant turns
        assembled_messages: list[dict] = [root_system_message, env_message]

        if memory_message:
            assembled_messages.append(memory_message)

        if summary_message:
            assembled_messages.append(summary_message)

        assembled_messages.extend(selected_recent)

        total_estimated = (
            system_tokens +
            env_tokens +
            tool_tokens +
            memory_tokens +
            summary_tokens +
            used_history_tokens
        )

        tokenizer_name = (
            "custom" if self.token_counter is not None
            else ("llama_server" if (self.models and self.models.is_running) else "calibrated_heuristic")
        )

        return ContextWindow(
            model=model_name,
            messages=assembled_messages,
            token_estimate=total_estimated,
            context_budget=budget,
            omitted_messages=len(omitted_messages),
            tokenizer=tokenizer_name
        )
