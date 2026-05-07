from __future__ import annotations

import uuid
from datetime import datetime, timezone

from tortoise import fields
from tortoise.models import Model


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Memory(Model):
    id = fields.UUIDField(pk=True, default=uuid.uuid4)
    user_id = fields.CharField(max_length=128, index=True)
    session_id = fields.CharField(max_length=128, null=True, index=True)
    summary = fields.TextField()
    source_history_ids = fields.JSONField(default=list)
    extra_metadata = fields.JSONField(default=dict, source_field="metadata")
    created_at = fields.DatetimeField(default=utc_now, index=True)
    indexed_at = fields.DatetimeField(null=True)

    class Meta:
        table = "memory"
        indexes = (("user_id", "session_id", "created_at"),)


class History(Model):
    id = fields.UUIDField(pk=True, default=uuid.uuid4)
    session_id = fields.CharField(max_length=128, index=True)
    user_id = fields.CharField(max_length=128, index=True)
    role = fields.CharField(max_length=32)
    content = fields.TextField()
    extra_metadata = fields.JSONField(default=dict, source_field="metadata")
    is_summarized = fields.BooleanField(default=False, index=True)
    token_count = fields.IntField(default=0)
    created_at = fields.DatetimeField(default=utc_now, index=True)
    indexed_at = fields.DatetimeField(null=True)

    class Meta:
        table = "history"
        indexes = (
            ("user_id", "session_id", "created_at"),
            ("user_id", "session_id", "is_summarized", "created_at"),
        )
