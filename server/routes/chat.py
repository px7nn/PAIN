import asyncio
import json
import threading

from fastapi           import APIRouter, Body, Request, HTTPException
from fastapi.responses import StreamingResponse

from core.engine    import Engine, EngineError
from server.schemas import ChatRequest, StopChatRequest, SummarizeRequest


router = APIRouter(prefix="/api/chat", tags=["Chat"])


def get_engine(engine: Engine):
    return engine


def get_engine_from_request(request: Request):
    return request.app.state.engine


@router.post("")
def chat(payload: ChatRequest, request: Request):
    engine = get_engine_from_request(request)

    attachments = [att.model_dump() for att in payload.attachments]

    try:
        result = engine.chat(
            message=payload.message,
            conversation_id=payload.conversation_id,
            model=payload.model,
            attachments=attachments
        )
        return result
    
    except EngineError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc)
        ) from exc


@router.post("/stream")
async def stream_chat(payload: ChatRequest, request: Request):
    engine = get_engine_from_request(request)

    # Create the conversation before streaming so its ID
    # can be sent to the frontend immediately
    conversation_id = payload.conversation_id

    if conversation_id is None:
        conversation_id = engine.create_conversation()

    elif not engine.conversations.get_conversation(conversation_id):
        raise HTTPException(
            status_code=404,
            detail="Conversation not found."
        )

    model_name = payload.model or engine.current_model
    attachments = [att.model_dump() for att in payload.attachments]
    request_id = payload.request_id

    cancel_event = threading.Event()
    engine.register_stream(cancel_event, request_id=request_id)

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[tuple[str, str | None]] = asyncio.Queue()

    def push(kind: str, value: str | None):
        try:
            loop.call_soon_threadsafe(queue.put_nowait, (kind, value))
        except RuntimeError:
            pass

    def worker():
        try:
            metadata = {
                "type": "start",
                "conversation_id": conversation_id,
                "model": model_name
            }
            push("data", f"data: {json.dumps(metadata)}\n\n")

            for event in engine.stream(
                message=payload.message,
                conversation_id=conversation_id,
                model=model_name,
                attachments=attachments,
                cancel_event=cancel_event,
                request_id=request_id
            ):
                if cancel_event.is_set():
                    break
                push("data", f"data: {json.dumps(event, default=str)}\n\n")

            if not cancel_event.is_set():
                try:
                    metrics = engine.get_context_metrics(conversation_id, model_name)
                    push("data", f"data: {json.dumps({'type': 'context_metrics', 'context': metrics}, default=str)}\n\n")
                except Exception:
                    pass

                push("data", 'data: {"type":"done"}\n\n')

        except Exception as exc:
            if not cancel_event.is_set():
                event = {
                    "type": "error",
                    "message": str(exc)
                }
                push("data", f"data: {json.dumps(event)}\n\n")
        finally:
            engine.unregister_stream(cancel_event)
            push("end", None)

    worker_thread = threading.Thread(target=worker, daemon=True)
    worker_thread.start()

    async def generate():
        try:
            while True:
                kind, value = await queue.get()
                if kind == "end":
                    break
                if cancel_event.is_set():
                    break
                if value is not None:
                    yield value
        except asyncio.CancelledError:
            engine.cancel_stream(cancel_event)
            raise
        finally:
            engine.cancel_stream(cancel_event)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@router.post("/stop")
def stop_chat(
    request: Request,
    payload: StopChatRequest | None = Body(default=None)
):
    """Immediately cancel the active streaming generation and free the model slot."""
    engine = get_engine_from_request(request)
    req_id = payload.request_id if payload is not None else None
    stopped = engine.stop_active_stream(request_id=req_id)
    return {"ok": True, "stopped": stopped}


@router.get("/context")
def get_context_metrics(
    request: Request,
    conversation_id: int | None = None,
    model: str | None = None
):
    """Retrieve real-time context metrics, budget, breakdown and llama-server telemetry"""
    engine = get_engine_from_request(request)
    try:
        return engine.get_context_metrics(conversation_id=conversation_id, model=model)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/summarize")
def summarize_conversation(payload: SummarizeRequest, request: Request):
    """Compact earlier conversation history into a dense summary, freeing up context tokens"""
    engine = get_engine_from_request(request)
    try:
        result = engine.summarize_conversation(
            conversation_id=payload.conversation_id,
            model=payload.model
        )
        return result
    except EngineError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Summarization error: {exc}") from exc