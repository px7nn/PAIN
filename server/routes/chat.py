import json

from fastapi           import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse

from core.engine    import Engine, EngineError
from server.schemas import ChatRequest, SummarizeRequest


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
def stream_chat(payload: ChatRequest, request: Request):
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

    def generate():
        metadata = {
            "type": "start",
            "conversation_id": conversation_id,
            "model": model_name
        }

        yield f"data: {json.dumps(metadata)}\n\n"

        try:
            for event in engine.stream(
                message=payload.message,
                conversation_id=conversation_id,
                model=model_name,
                attachments=attachments
            ):
                yield f"data: {json.dumps(event, default=str)}\n\n"

            # Emit live real-time context token metrics immediately upon completion
            try:
                metrics = engine.get_context_metrics(conversation_id, model_name)
                yield f"data: {json.dumps({'type': 'context_metrics', 'context': metrics}, default=str)}\n\n"
            except Exception:
                pass

            yield 'data: {"type":"done"}\n\n'

        except Exception as exc:
            event = {
                "type": "error",
                "message": str(exc)
            }
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


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