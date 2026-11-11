"""Tags of tracks (app/tags.py): the list of them, a track's tags, putting them on and taking them off."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import tags
from app.auth import current_user
from app.db import get_session
from app.models import TagSelection, User

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


class TagMergeIn(BaseModel):
    into: int


@router.put("/{tag_id}", response_model=TagsOut)
async def rename(tag_id: int, body: TagCreateIn, user: User = Depends(current_user),
                 s: AsyncSession = Depends(get_session, scope="function")):
    """Another name for a tag of one's own."""
    try:
        await tags.rename(s, user.id, tag_id, body.name)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    await s.commit()
    return await _out(s, user)


@router.post("/{tag_id}/merge", response_model=TagsOut)
async def merge(tag_id: int, body: TagMergeIn, user: User = Depends(current_user),
                s: AsyncSession = Depends(get_session, scope="function")):
    """Two tags of one's own become one: this one's tracks get `into`, and it is gone."""
    try:
        await tags.merge(s, user.id, tag_id, body.into)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    await s.commit()
    return await _out(s, user)


@router.delete("/{tag_id}", response_model=TagsOut)
async def delete(tag_id: int, user: User = Depends(current_user),
                 s: AsyncSession = Depends(get_session, scope="function")):
    """A tag of one's own is gone from every track."""
    try:
        await tags.remove(s, user.id, tag_id)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    await s.commit()
    return await _out(s, user)


# --- «Подборки по тегам»: saved filters --------------------------------------------------------------------


class SelectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    scope: str = Field(pattern=r"^(liked|library|playlist:.{1,64})$")
    filter: dict[str, str]  # tag id / "untagged" -> yes / no


class SelectionOut(SelectionIn):
    id: int


def _selection(row: TagSelection) -> SelectionOut:
    return SelectionOut(id=row.id, name=row.name, scope=row.scope, filter=dict(row.filter_json or {}))


def _filter(value: dict[str, str]) -> dict[str, str]:
    out = {k: v for k, v in value.items() if v in ("yes", "no") and (k == "untagged" or k.isdigit())}
    if not out:
        raise HTTPException(400, "В подборке должен быть хотя бы один тег")
    return out


async def _own_selection(s: AsyncSession, user: User, selection_id: int) -> TagSelection:
    row = await s.get(TagSelection, selection_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(404, "Подборка не найдена")
    return row


@router.get("/selections/all", response_model=list[SelectionOut])
async def selections(user: User = Depends(current_user), s: AsyncSession = Depends(get_session, scope="function")):
    rows = (await s.execute(select(TagSelection).where(TagSelection.user_id == user.id).order_by(TagSelection.name))).scalars()
    return [_selection(r) for r in rows]


@router.post("/selections", response_model=SelectionOut)
async def create_selection(body: SelectionIn, user: User = Depends(current_user),
                           s: AsyncSession = Depends(get_session, scope="function")):
    row = TagSelection(user_id=user.id, name=" ".join(body.name.split()), scope=body.scope, filter_json=_filter(body.filter))
    s.add(row)
    await s.commit()
    return _selection(row)


@router.put("/selections/{selection_id}", response_model=SelectionOut)
async def save_selection(selection_id: int, body: SelectionIn, user: User = Depends(current_user),
                         s: AsyncSession = Depends(get_session, scope="function")):
    row = await _own_selection(s, user, selection_id)
    row.name, row.scope, row.filter_json = " ".join(body.name.split()), body.scope, _filter(body.filter)
    await s.commit()
    return _selection(row)


@router.delete("/selections/{selection_id}")
async def delete_selection(selection_id: int, user: User = Depends(current_user),
                           s: AsyncSession = Depends(get_session, scope="function")):
    await s.delete(await _own_selection(s, user, selection_id))
    await s.commit()
    return {"ok": True}
