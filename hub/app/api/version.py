"""The Hub's version and «Что нового» (`app.version`); for the admin, what it is built of («О сервере»)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app import version
from app.auth import admin_user, current_user
from app.config import get_settings
from app.models import User

router = APIRouter(tags=["version"])


class ReleaseSection(BaseModel):
    title: str
    items: list[str]


class Release(BaseModel):
    version: str
    date: str | None
    intro: str
    sections: list[ReleaseSection]


class VersionOut(BaseModel):
    version: str
    releases: list[Release]


class Component(BaseModel):
    name: str
    version: str | None


class AboutOut(BaseModel):
    version: str
    commit: str | None
    built_at: str | None
    role: str
    components: list[Component]


@router.get("/api/version", response_model=VersionOut)
async def current(_: User = Depends(current_user)):
    return VersionOut(version=version.version(), releases=[Release.model_validate(r) for r in version.releases()])


@router.get("/api/admin/about", response_model=AboutOut)
async def about(_: User = Depends(admin_user)):
    b = version.build()
    return AboutOut(version=version.version(), commit=b["commit"], built_at=b["built_at"],
                    role=get_settings().hub_role,
                    components=[Component.model_validate(c) for c in await version.components()])
