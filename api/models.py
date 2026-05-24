from __future__ import annotations

import uuid

from tortoise import fields
from tortoise.models import Model

from memory.models import utc_now, uuid_v7


class ApiSession(Model):
    id = fields.UUIDField(pk=True, default=uuid_v7)
    user_id = fields.CharField(max_length=128, index=True)
    memory_session_id = fields.UUIDField(index=True)
    project_root = fields.TextField()
    continue_maintenance = fields.BooleanField(default=False)
    phase = fields.CharField(max_length=32, default="requirements")
    confirmed_requirements = fields.TextField(null=True)
    confirmed_prototype = fields.TextField(null=True)
    backend_result = fields.TextField(null=True)
    requirements_form = fields.JSONField(null=True)
    created_at = fields.DatetimeField(default=utc_now, index=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "api_session"
        indexes = (("user_id", "created_at"),)


class ApiSessionMessage(Model):
    id = fields.UUIDField(pk=True, default=uuid_v7)
    api_session_id = fields.UUIDField(index=True)
    role = fields.CharField(max_length=32)
    content = fields.TextField()
    agent = fields.CharField(max_length=128, null=True)
    phase = fields.CharField(max_length=32, null=True)
    created_at = fields.DatetimeField(default=utc_now, index=True)

    class Meta:
        table = "api_session_message"
        indexes = (("api_session_id", "created_at"),)

    @staticmethod
    def session_uuid(session_id: str | uuid.UUID) -> uuid.UUID:
        return session_id if isinstance(session_id, uuid.UUID) else uuid.UUID(str(session_id))
