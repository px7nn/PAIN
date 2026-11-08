from fastapi import APIRouter, HTTPException, Request

from server.schemas import (
    CreateConversationRequest,
    RenameConversationRequest
)


router = APIRouter(
    prefix="/api/conversations",
    tags=["Conversations"]
)


@router.get("")
def list_conversations(request: Request):

    engine = request.app.state.engine

    return engine.list_conversations()


@router.post("")
def create_conversation(
    payload: CreateConversationRequest,
    request: Request
):
    engine = request.app.state.engine

    conversation_id = engine.create_conversation(
        title=payload.title
    )

    return {
        "id": conversation_id,
        "title": payload.title
    }


@router.get("/{conversation_id}")
def get_conversation(
    conversation_id: int,
    request: Request
):
    engine = request.app.state.engine

    try:
        return engine.get_conversation(conversation_id)

    except Exception as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc)
        ) from exc


@router.patch("/{conversation_id}")
def rename_conversation(
    conversation_id: int,
    payload: RenameConversationRequest,
    request: Request
):
    engine = request.app.state.engine

    conversation = engine.conversations.get_conversation(
        conversation_id
    )

    if conversation is None:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found."
        )

    engine.conversations.rename_conversation(
        conversation_id,
        payload.title
    )

    return {
        "id": conversation_id,
        "title": payload.title
    }


@router.delete("/{conversation_id}")
def delete_conversation(
    conversation_id: int,
    request: Request
):
    engine = request.app.state.engine

    conversation = engine.conversations.get_conversation(
        conversation_id
    )

    if conversation is None:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found."
        )

    engine.conversations.delete_conversation(
        conversation_id
    )

    return {
        "success": True,
        "deleted_id": conversation_id
    }