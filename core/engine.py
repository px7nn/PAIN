import http.client
import json
import socket
import threading

from urllib.parse   import urlsplit
from urllib.request import Request, urlopen
from urllib.error   import HTTPError, URLError

from core.config        import config, Config
from core.model_manager import ModelManager
from core.conversation  import ConversationManager
from core.context       import ContextManager
from core.memory        import MemoryManager
from core.tool_manager  import ToolManager
from core.tool_loader   import ToolLoader
from core.agent         import Agent


class EngineError(Exception):
    """Raised when PAIN engine operations fail"""
    pass


class Engine:
    def __init__(self, cfg: Config = config):
        self.config = cfg

        # Core components
        self.models        = ModelManager(cfg)
        self.conversations = ConversationManager()
        self.memory        = MemoryManager()

        self.context       = ContextManager(
            conversations=self.conversations,
            cfg=cfg,
            models=self.models
        )
        
        self.tool_manager  = ToolManager()
        self.tool_loader   = ToolLoader(
            self.tool_manager,
            dependencies={"memory": self.memory}
        )

        self.tool_status = self.tool_loader.load_all()
        
        self.agent = Agent(
            tool_manager=self.tool_manager,
            generate=self._generate,
            generate_stream=self._stream_generate
        )
        
        # Default model
        self.current_model = cfg.inference.default_model

        # Serialize inference and model switching
        self.lock = threading.RLock()

        # Track active streaming requests for immediate cancellation
        self._streams_lock = threading.Lock()
        self._active_streams: dict[int, dict] = {}

        self._closed = False

    def register_stream(
        self,
        cancel_event: threading.Event,
        request_id: str | None = None
    ) -> int:
        key = id(cancel_event)
        with self._streams_lock:
            self._active_streams.setdefault(
                key,
                {
                    "cancel_event": cancel_event,
                    "request_id": request_id,
                    "sock": None,
                }
            )
        return key

    def unregister_stream(self, cancel_event: threading.Event):
        key = id(cancel_event)
        with self._streams_lock:
            self._active_streams.pop(key, None)

    def _set_stream_socket(self, cancel_event: threading.Event | None, sock):
        if cancel_event is None:
            return
        key = id(cancel_event)
        with self._streams_lock:
            entry = self._active_streams.get(key)
            if entry is not None:
                entry["sock"] = sock

    def cancel_stream(self, cancel_event: threading.Event | None):
        if cancel_event is None:
            return
        cancel_event.set()
        key = id(cancel_event)
        with self._streams_lock:
            entry = self._active_streams.get(key)
            sock = entry.get("sock") if entry is not None else None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def stop_active_stream(self, request_id: str | None = None) -> bool:
        with self._streams_lock:
            targets = []
            for entry in self._active_streams.values():
                if request_id is None or entry.get("request_id") == request_id:
                    targets.append((entry.get("cancel_event"), entry.get("sock")))

        stopped = False
        for cancel_event, sock in targets:
            if cancel_event is not None:
                cancel_event.set()
                stopped = True
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        return stopped

    # --------------------------------------------------
    # MODEL MANAGEMENT
    # --------------------------------------------------

    def _ensure_model(self, model_name: str):
        self.config.get_model(model_name)
        if (
            self.models.is_running
            and self.models.active_model == model_name
        ):
            return

        self.models.start(model_name)
        self.current_model = model_name

    def switch_model(self, model_name: str):
        with self.lock:
            if self._closed:
                raise EngineError("Engine is closed.")

            self.config.get_model(model_name)
            self.models.switch(model_name)

            self.current_model = model_name

            return self.models.status()

    def get_status(self) -> dict:
        return {
            "model": self.current_model,
            "inference": self.models.status(),
            "conversations": "available",
            "memory": "available",
            "tools": {
                "loaded": self.tool_loader.loaded(),
                "failed": self.tool_loader.failed(),
                "tools":  self.tool_manager.list_tools()
            }
        }

    # --------------------------------------------------
    # CONVERSATION MANAGEMENT
    # --------------------------------------------------
    
    def _retrieve_memories(self, max_memories: int = 2) -> str:
        """Retrieve only high-priority pinned memories for baseline continuity.
        Dynamic contextual recall is handled on-demand via the search_memory tool."""
        try:
            pinned = self.memory.list(limit=10)
            selected = [
                m for m in pinned
                if m.get("pinned") and m.get("importance", 0) >= 4
            ][:max_memories]

            if not selected:
                return ""

            sections = []
            total_chars = 0
            for item in selected:
                snippet = f"[#{item['id']} | {item['category']}] {item['content']}"
                if total_chars + len(snippet) > 300:
                    break
                sections.append(snippet)
                total_chars += len(snippet)

            return "\n".join(sections)
        except Exception:
            return ""

    def _build_context(self, conversation_id: int, model_name: str):
        memory_context = self._retrieve_memories()
        available_tools = self.tool_manager.get_tools()
        return self.context.build(
            conversation_id=conversation_id,
            model_name=model_name,
            memory_context=memory_context,
            tools=available_tools
        )

    def create_conversation(
        self,
        title: str = "New conversation"
    ) -> int:
        return self.conversations.create_conversation(title)

    def list_conversations(self) -> list[dict]:
        return self.conversations.list_conversations()

    def get_conversation(self, conversation_id: int) -> dict:
        conversation = self.conversations.get_conversation(conversation_id)

        if conversation is None:
            raise EngineError(f"Conversation not found: {conversation_id}")

        conversation["messages"] = (self.conversations.get_messages(conversation_id))
        return conversation

    # --------------------------------------------------
    # INFERENCE
    # --------------------------------------------------

    def _generate(
        self,
        messages: list[dict],
        model_name: str,
        tools: list[dict] | None = None
    ) -> dict:
        inference = self.config.inference

        payload = {
            "model"    : model_name,
            "messages" : messages,

            "temperature" : inference.temperature,
            "top_p"       : inference.top_p,
            "top_k"       : inference.top_k,
            "min_p"       : inference.min_p,

            "max_tokens" : inference.max_tokens,
            "stream"     : False
        }
        if tools:
            payload["tools"]               = tools
            payload["tool_choice"]         = "auto"
            payload["parallel_tool_calls"] = False
            
            
        body = json.dumps(payload).encode("utf-8")

        request = Request(
            f"{self.models.base_url}/v1/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json"
            },
            method="POST"
        )

        try:
            with urlopen(request, timeout=600) as response:
                result = json.loads(response.read().decode("utf-8"))

        except HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise EngineError(f"llama-server returned HTTP {exc.code}: {error_body}") from exc

        except URLError as exc:
            raise EngineError(f"Unable to connect to llama-server: {exc.reason}") from exc

        except TimeoutError as exc:
            raise EngineError("Inference request timed out.") from exc

        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise EngineError("Invalid response from llama-server.") from exc

        try:
            choice = result["choices"][0]
            message = choice["message"]

            if not isinstance(message, dict):
                raise EngineError("Invalid assistant message.")
            
            return {
                "message": message,
                "finish_reason": choice.get("finish_reason"),
                "usage": result.get("usage", {})
            }
        except (KeyError, IndexError, TypeError) as exc:
            raise EngineError("Unexpected llama-server response format.") from exc
        
    def _stream_generate(
        self,
        messages: list[dict],
        model_name: str,
        tools: list[dict] | None = None,
        cancel_event: threading.Event | None = None
    ):
        if cancel_event is not None and cancel_event.is_set():
            return

        inference = self.config.inference

        payload = {
            "model"    : model_name,
            "messages" : messages,

            "temperature" : inference.temperature,
            "top_p"       : inference.top_p,
            "top_k"       : inference.top_k,
            "min_p"       : inference.min_p,
            "max_tokens"  : inference.max_tokens,

            "stream": True
        }

        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
            payload["parallel_tool_calls"] = False

        body = json.dumps(payload).encode("utf-8")

        parsed = urlsplit(self.models.base_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        endpoint = f"{parsed.path.rstrip('/')}/v1/chat/completions"

        conn_cls = (
            http.client.HTTPSConnection
            if parsed.scheme == "https"
            else http.client.HTTPConnection
        )
        conn = conn_cls(host, port, timeout=600)
        response = None

        try:
            conn.connect()
            self._set_stream_socket(cancel_event, conn.sock)

            if cancel_event is not None and cancel_event.is_set():
                return

            conn.request(
                "POST",
                endpoint,
                body=body,
                headers={
                    "Content-Type": "application/json",
                    "Connection": "close",
                }
            )
            response = conn.getresponse()

            if response.status >= 400:
                error_body = response.read().decode("utf-8", errors="replace")
                raise EngineError(f"llama-server returned HTTP {response.status}: {error_body}")

            for raw_line in response:
                if cancel_event is not None and cancel_event.is_set():
                    break

                line = raw_line.decode("utf-8", errors="replace").strip()

                if not line.startswith("data:"):
                    continue

                data = line[5:].strip()

                if data == "[DONE]":
                    break

                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue

                choices = chunk.get("choices", [])

                if not choices:
                    if chunk.get("usage"):
                        yield {"usage": chunk["usage"]}
                    continue

                choice = choices[0]
                yield {
                    "delta": choice.get("delta") or {},
                    "finish_reason": choice.get("finish_reason"),
                    "usage": chunk.get("usage", {})
                }

        except TimeoutError as exc:
            if cancel_event is not None and cancel_event.is_set():
                return
            raise EngineError("Streaming request timed out.") from exc

        except (http.client.HTTPException, OSError) as exc:
            if cancel_event is not None and cancel_event.is_set():
                return
            raise EngineError(f"Unable to connect to llama-server: {exc}") from exc

        finally:
            self._set_stream_socket(cancel_event, None)
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
            try:
                conn.close()
            except Exception:
                pass

    # --------------------------------------------------
    # CHAT
    # --------------------------------------------------

    def chat(
        self,
        message: str = "",
        conversation_id: int | None = None,
        model: str | None = None,
        attachments: list[dict] | None = None
    ) -> dict:
        cleaned_attachments = [
            att for att in (attachments or [])
            if isinstance(att, dict)
        ]
        if not isinstance(message, str) or (not message.strip() and not cleaned_attachments):
            raise EngineError("Message cannot be empty.")

        with self.lock:
            if self._closed:
                raise EngineError("Engine is closed.")

            model_name = model or self.current_model

            self.config.get_model(model_name)

            # Create conversation if necessary
            if conversation_id is None:
                conversation_id = self.create_conversation()
            else:
                if not self.conversations.get_conversation(conversation_id):
                    raise EngineError(f"Conversation not found: {conversation_id}")

            # Start or switch model
            self._ensure_model(model_name)

            # Save user message first
            self.conversations.add_message(
                conversation_id=conversation_id,
                role="user",
                content=message,
                attachments=cleaned_attachments or None
            )

            # Construct context
            context = self._build_context(conversation_id, model_name)

            # Generate response
            result = self.agent.run(
                messages=context.messages,
                model_name=model_name
            )

            # Persist assistant response
            self.conversations.add_message(
                conversation_id=conversation_id,
                role="assistant",
                content=result.content,
                model=model_name
            )

            return {
                "conversation_id": conversation_id,
                
                "model"    : model_name,
                "response" : result.content,
                
                "finish_reason" : result.finish_reason,
                "usage"  : result.usage,
                
                "tool_calls" : result.tool_calls,
                
                "context": {
                    "estimated_tokens": context.token_estimate,
                    "budget": context.context_budget,
                    "omitted_messages": context.omitted_messages
                }
            }
            
    def stream(
        self,
        message: str = "",
        conversation_id: int | None = None,
        model: str | None = None,
        attachments: list[dict] | None = None,
        cancel_event: threading.Event | None = None,
        request_id: str | None = None
    ):
        cleaned_attachments = [
            att for att in (attachments or [])
            if isinstance(att, dict)
        ]
        if not isinstance(message, str) or (not message.strip() and not cleaned_attachments):
            raise EngineError("Message cannot be empty.")

        cancel_event = cancel_event or threading.Event()
        self.register_stream(cancel_event, request_id=request_id)

        try:
            if cancel_event.is_set():
                return

            with self.lock:
                if self._closed:
                    raise EngineError("Engine is closed.")

                if cancel_event.is_set():
                    return

                model_name = model or self.current_model
                self.config.get_model(model_name)

                if conversation_id is None:
                    conversation_id = self.create_conversation()
                elif not self.conversations.get_conversation(conversation_id):
                    raise EngineError(f"Conversation not found: {conversation_id}")

                self._ensure_model(model_name)
                if cancel_event.is_set():
                    return

                self.conversations.add_message(
                    conversation_id,
                    "user",
                    message,
                    attachments=cleaned_attachments or None
                )

                context = self._build_context(conversation_id, model_name)
                if cancel_event.is_set():
                    return

                response_parts = []
                completed = False

                try:
                    for event in self.agent.stream(
                        messages=context.messages,
                        model_name=model_name,
                        cancel_event=cancel_event
                    ):
                        if cancel_event.is_set():
                            break
                        if event.get("type") == "token":
                            response_parts.append(event.get("content", ""))
                        yield event

                    if not cancel_event.is_set():
                        completed = True

                finally:
                    partial_text = "".join(response_parts)
                    if (completed or cancel_event.is_set()) and partial_text.strip():
                        self.conversations.add_message(
                            conversation_id,
                            "assistant",
                            partial_text,
                            model=model_name
                        )
        finally:
            self.unregister_stream(cancel_event)

    # --------------------------------------------------
    # CONTEXT METRICS & SUMMARIZATION
    # --------------------------------------------------

    def get_context_metrics(
        self,
        conversation_id: int | None = None,
        model: str | None = None
    ) -> dict:
        """Fetch combined real-time context token metrics and llama-server telemetry"""
        model_name = model or self.current_model
        server_metrics = self.models.get_metrics()

        if conversation_id is None:
            model_info = self.config.get_model(model_name)
            ctx_size = model_info.context_size
            return {
                "conversation_id": None,
                "model": model_name,
                "context_size": ctx_size,
                "context_budget": ctx_size - self.config.inference.max_tokens - 256,
                "context_used": 0,
                "context_available": ctx_size,
                "context_percent": 0.0,
                "color_level": "ok",
                "breakdown": {
                    "system": 0,
                    "env": 0,
                    "tools": 0,
                    "memory": 0,
                    "summary": 0,
                    "history": 0
                },
                "server_metrics": server_metrics,
                "has_summary": False,
                "summary_text": None,
                "can_summarize": False,
                "message_count": 0,
                "omitted_messages": 0
            }

        metrics = self.context.get_context_metrics(
            conversation_id=conversation_id,
            model_name=model_name,
            tools=self.tool_manager.get_tools()
        )
        metrics["server_metrics"] = server_metrics
        return metrics

    def summarize_conversation(
        self,
        conversation_id: int,
        model: str | None = None
    ) -> dict:
        """Compact conversation by summarizing earlier history into dense prose"""
        with self.lock:
            if self._closed:
               raise EngineError("Engine is closed.")

            model_name = model or self.current_model
            self.config.get_model(model_name)

            conversation = self.conversations.get_conversation(conversation_id)
            if conversation is None:
                raise EngineError(f"Conversation not found: {conversation_id}")

            raw_messages = self.conversations.get_messages(conversation_id)
            chat_messages = [m for m in raw_messages if m["role"] in ("user", "assistant")]
            if len(chat_messages) < 2:
                raise EngineError("At least 2 messages are required to summarize a conversation.")

            # Measure token usage before summarization
            metrics_before = self.get_context_metrics(conversation_id, model_name)
            tokens_before = metrics_before["context_used"]

            # Cut messages: preserve the most recent 2 turns (or last 2 messages) if there are > 4 messages
            if len(chat_messages) > 4:
                to_summarize = chat_messages[:-2]
            else:
                to_summarize = chat_messages

            transcript_lines = []
            for m in to_summarize:
                speaker = "User" if m["role"] == "user" else "Assistant"
                content = (m["content"] or "").strip()
                if content:
                    transcript_lines.append(f"{speaker}: {content}")

            transcript = "\n\n".join(transcript_lines)
            compaction_prompt = (
                "Summarize this conversation so it can seamlessly continue in a fresh context. "
                "Capture: the overall goal, key facts and decisions, the current state of any ongoing work "
                "(including code, file names, and data discussed), and what remains to be done. "
                "Write the summary as dense prose addressed to the assistant that will resume the conversation. "
                "Return ONLY the summary, nothing else."
            )

            summary = ""
            try:
                self._ensure_model(model_name)
                prompt_messages = [
                    {
                        "role": "system",
                        "content": "You are a concise technical summarizer. Provide high-density, accurate context summaries."
                    },
                    {
                        "role": "user",
                        "content": f"[Conversation Transcript]\n{transcript}\n\n[Instruction]\n{compaction_prompt}"
                    }
                ]
                result = self._generate(prompt_messages, model_name=model_name)
                summary = (result.get("message", {}).get("content") or "").strip()
            except Exception:
                # Fallback extractive summary
                lines = []
                for m in to_summarize:
                    c = m["content"].strip().splitlines()[0][:100]
                    lines.append(f"• {m['role'].capitalize()}: {c}")
                summary = "\n".join(lines[-8:])

            if not summary:
                raise EngineError("Failed to generate conversation summary.")

            # Update summary in conversation record
            self.conversations.update_summary(conversation_id, summary)

            # Insert system landmark into conversation
            self.conversations.add_message(
                conversation_id=conversation_id,
                role="system",
                content=f"[Conversation Compacted]\nSummary of earlier exchange:\n{summary}"
            )

            # Measure token usage after summarization
            metrics_after = self.get_context_metrics(conversation_id, model_name)
            tokens_after = metrics_after["context_used"]

            return {
                "ok": True,
                "conversation_id": conversation_id,
                "model": model_name,
                "summary": summary,
                "tokens_before": tokens_before,
                "tokens_after": tokens_after,
                "saved_tokens": max(0, tokens_before - tokens_after),
                "context": metrics_after
            }

    # --------------------------------------------------
    # SHUTDOWN
    # --------------------------------------------------

    def close(self):
        self.stop_active_stream()
        with self.lock:
            if self._closed:
                return

            self.models.stop()
            
            self.conversations.close()
            self.memory.close()
            
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()