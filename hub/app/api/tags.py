"""Tags of tracks (app/tags.py): the list of them, a track's tags, putting them on and taking them off."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app import tags
from app.auth import current_user
from app.db import get_session
from app.models import User

router = APIRouter(prefix="/api/tags", tags=["tags"])


class TagOut(BaseModel):
    id: int
    group: str  # lang / genre / own
    name: str
    code: str | None = None  # of a language («ja»), «instrumental»
    count: int = 0  # library tracks that have it


class TagsOut(BaseModel):
    groups: list[dict[str, Any]]  # {id, title, tags: [TagOut]}
    map: dict[str, list[int]]  # Navidrome song id -> its tags


class TagCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class TagAssignIn(BaseModel):
    nd_song_ids: list[str] = Field(min_length=1, max_length=5000)
    add: list[int] = Field(default_factory=list, max_length=100)
    remove: list[int] = Field(default_factory=list, max_length=100)


async def _out(s: AsyncSession, user: User) -> TagsOut:
    return TagsOut(groups=(await tags.catalog(s, user.id))["groups"], map=await tags.tag_map(s, user.id))


@router.get("", response_model=TagsOut)
async def all_tags(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    """The user's tags by group and the tags of every library track: the page filters its lists by them itself."""
    return await _out(s, user)


@router.post("", response_model=TagOut)
async def create(body: TagCreateIn, user: User = Depends(current_user),
                 s: AsyncSession = Depends(get_session, scope="function")):
    """A tag of the user's own (an existing one of that name is given back)."""
    name = " ".join(body.name.split())
    if not name:
        raise HTTPException(400, "Введите название тега")
    tag = await tags.get_or_create(s, "own", name, user_id=user.id)
    await s.commit()
    return TagOut(id=tag.id, group=tag.group, name=tag.name, code=tag.code)


@router.post("/assign", response_model=TagsOut)
async def assign(body: TagAssignIn, user: User = Depends(current_user),
                 s: AsyncSession = Depends(get_session, scope="function")):
    """Tags onto tracks and off them, several tracks at once."""
    await tags.assign(s, user.id, list(dict.fromkeys(body.nd_song_ids)), body.add, body.remove)
    await s.commit()
    return await _out(s, user)
