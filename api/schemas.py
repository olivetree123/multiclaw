from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class AgentReplyModel(BaseModel):
    role: str = Field(description="消息类型：user、status 或 assistant")
    content: str
    agent: str | None = None


class SessionMessageModel(BaseModel):
    id: str
    role: str
    content: str
    agent: str | None = None
    phase: str | None = None
    created_at: datetime


class CreateSessionRequest(BaseModel):
    project_root: str = Field(description="项目根目录路径")
    continue_maintenance: bool = Field(
        default=False,
        description="跳过开发流程，直接进入维护阶段",
    )
    memory_session_id: str | None = Field(
        default=None,
        description="可选，复用已有 memory 会话 UUID",
    )


class CreateSessionResponse(BaseModel):
    session_id: str = Field(description="API 会话 ID，后续请求使用")
    memory_session_id: str = Field(description="底层 memory 会话 UUID")
    phase: str
    project_root: str
    messages: list[AgentReplyModel]


class SessionSummaryModel(BaseModel):
    session_id: str
    memory_session_id: str
    phase: str
    project_root: str
    continue_maintenance: bool
    message_count: int
    created_at: datetime
    updated_at: datetime


class SessionListResponse(BaseModel):
    sessions: list[SessionSummaryModel]
    total: int


class SessionInfoResponse(BaseModel):
    session_id: str
    memory_session_id: str
    phase: str
    project_root: str
    continue_maintenance: bool
    message_count: int
    requirements_form: dict | None = None
    created_at: datetime
    updated_at: datetime


class UpdateSessionRequest(BaseModel):
    project_root: str = Field(description="新的项目根目录路径")


class UpdateSessionResponse(BaseModel):
    session_id: str
    memory_session_id: str
    phase: str
    project_root: str
    continue_maintenance: bool
    message_count: int
    created_at: datetime
    updated_at: datetime


class SessionMessagesResponse(BaseModel):
    session_id: str
    total: int
    messages: list[SessionMessageModel]


class SubmitMessageRequest(BaseModel):
    message: str = Field(min_length=1, description="用户消息")
    stream: bool = Field(default=False, description="为 true 时通过 SSE 流式返回执行过程")


class SubmitMessageResponse(BaseModel):
    session_id: str
    memory_session_id: str
    phase: str
    phase_changed: bool
    requirements_form: dict | None = None
    messages: list[AgentReplyModel]
