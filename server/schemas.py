from typing import Any

from pydantic import BaseModel, Field, model_validator


class AttachmentPayload(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    extension: str = ""
    format: str = "file"
    method: str = "extracted"
    text: str = ""
    char_count: int = 0
    truncated: bool = False
    meta: dict[str, Any] = Field(default_factory=dict)


class ChatRequest(BaseModel):
    message: str = ""
    conversation_id: int | None = None
    model: str | None = None
    request_id: str | None = None
    attachments: list[AttachmentPayload] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def validate_message_or_attachments(self):
        if not self.message.strip() and not self.attachments:
            raise ValueError("Message or at least one attachment is required.")
        return self


class StopChatRequest(BaseModel):
    request_id: str | None = None


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


class CreateMemoryRequest(BaseModel):
    category: str = Field(min_length=1)
    content: str = Field(min_length=1, max_length=4000)
    importance: int = Field(default=3, ge=1, le=5)
    pinned: bool = False
    tags: list[str] = Field(default_factory=list, max_length=20)
    source: str | None = "manual"


class UpdateMemoryRequest(BaseModel):
    category: str | None = None
    content: str | None = Field(default=None, min_length=1, max_length=4000)
    importance: int | None = Field(default=None, ge=1, le=5)
    pinned: bool | None = None
    tags: list[str] | None = Field(default=None, max_length=20)
