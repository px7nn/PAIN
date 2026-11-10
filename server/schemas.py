from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    conversation_id: int | None = None
    model: str | None = None


class CreateConversationRequest(BaseModel):
    title: str = Field(
        default="New conversation",
        min_length=1,
        max_length=120
    )


class RenameConversationRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class SwitchModelRequest(BaseModel):
    model: str = Field(min_length=1)


class SummarizeRequest(BaseModel):
    conversation_id: int
    model: str | None = None
