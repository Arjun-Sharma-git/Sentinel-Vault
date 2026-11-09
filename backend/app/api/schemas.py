"""Request and response bodies."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..auth import MIN_PASSWORD_LENGTH

# Deliberately simple: the address is an identifier here, not something we send mail to.
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class LoginRequest(BaseModel):
    email: str = Field(max_length=254, pattern=EMAIL_PATTERN)
    password: str = Field(min_length=1, max_length=256)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    # Knowledge sources to search (backend/sources/*.yaml names); omitted: everything readable.
    sources: list[str] | None = Field(default=None, max_length=20)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    name: str
    is_admin: bool
    is_active: bool
    created_at: datetime


class MeOut(UserOut):
    groups: list[str]


class UserCreate(BaseModel):
    email: str = Field(max_length=254, pattern=EMAIL_PATTERN)
    name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=256)
    is_admin: bool = False
    group_ids: list[str] = []


class UserUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    password: str | None = Field(default=None, min_length=MIN_PASSWORD_LENGTH, max_length=256)
    is_admin: bool | None = None
    is_active: bool | None = None
    group_ids: list[str] | None = None


class GroupOut(BaseModel):
    id: str
    name: str
    description: str
    member_ids: list[str]


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9 _.-]+$")
    description: str = Field(default="", max_length=300)


class FolderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    path: str
    name: str
    description: str
    parent_id: str | None


class FolderCreate(BaseModel):
    # A new top-level folder or subfolder; created on disk too.
    path: str = Field(min_length=1, max_length=200, pattern=r"^[a-z0-9][a-z0-9_-]*(/[a-z0-9][a-z0-9_-]*)*$")
    description: str = Field(default="", max_length=300)


class GrantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    folder_id: str
    subject_type: Literal["user", "group"]
    subject_id: str
    relation: Literal["viewer", "editor"]
    created_at: datetime


class GrantCreate(BaseModel):
    folder_id: str
    subject_type: Literal["user", "group"]
    subject_id: str
    relation: Literal["viewer", "editor"] = "viewer"


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    folder_id: str
    rel_path: str
    title: str
    mime_type: str
    size_bytes: int
    status: str
    error: str | None
    chunk_count: int
    source: str
    indexed_at: datetime | None
    updated_at: datetime
