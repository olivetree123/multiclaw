from __future__ import annotations

from tortoise import fields
from tortoise.models import Model

from memory.models import utc_now, uuid_v7


class User(Model):
    id = fields.UUIDField(pk=True, default=uuid_v7)
    username = fields.CharField(max_length=64, unique=True, index=True)
    email = fields.CharField(max_length=255, unique=True, null=True)
    hashed_password = fields.CharField(max_length=255)
    is_active = fields.BooleanField(default=True)
    created_at = fields.DatetimeField(default=utc_now)

    class Meta:
        table = "users"

    @property
    def memory_user_id(self) -> str:
        return str(self.id)
