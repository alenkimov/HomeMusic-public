"""Storage and ownership rules for the Hub's service credentials."""

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models import AppSetting, Import, IntegrationConnection, IntegrationCredential, utcnow
from app.security import decrypt, encrypt

SHAREABLE = frozenset({"yandex", "spotify"})


async def credential(s: AsyncSession, connection_id: int) -> str | None:
    row = await s.get(IntegrationCredential, connection_id)
    return decrypt(row.encrypted) if row else None


async def save(s: AsyncSession, *, provider: str, owner_id: int | None, secret: str,
               subject: str | None = None, account: str | None = None, plan: str | None = None,
               shared: bool | None = None, retained: bool = True) -> IntegrationConnection:
    now = utcnow()
    visible = provider == "lastfm" or (bool(shared) and provider in SHAREABLE)
    statement = insert(IntegrationConnection).values(
        provider=provider, owner_user_id=owner_id, subject=subject, account=account, shared=visible,
        retained=retained, state="connected", plan=plan, connected_at=now, checked_at=now if retained else None)
    changes = {"account": account, "state": "connected", "plan": plan, "checked_at": now, "retained": True}
    if shared is not None:
        changes["shared"] = visible
    if provider == "lastfm":
        statement = statement.on_conflict_do_update(index_elements=["provider"],
                                                    index_where=IntegrationConnection.provider == "lastfm",
                                                    set_=changes)
    elif subject and retained:
        statement = statement.on_conflict_do_update(index_elements=["provider", "owner_user_id", "subject"],
                                                    set_=changes)
    row = (await s.execute(statement.returning(IntegrationConnection),
                           execution_options={"populate_existing": True})).scalar_one()
    enc = encrypt(secret)
    await s.execute(insert(IntegrationCredential).values(connection_id=row.id, encrypted=enc)
                    .on_conflict_do_update(index_elements=["connection_id"], set_={"encrypted": enc}))
    return row


async def set_import_credential(s: AsyncSession, imp: Import, secret: str) -> None:
    connection = await save(s, provider=imp.source, owner_id=imp.user_id, secret=secret, retained=False)
    imp.connection_id = connection.id


async def import_connection(s: AsyncSession, imp: Import) -> IntegrationConnection | None:
    if imp.connection_id is None:
        return None
    row = await s.get(IntegrationConnection, imp.connection_id)
    return row if row and row.owner_user_id == imp.user_id and row.provider == imp.source else None


async def release_import(s: AsyncSession, imp: Import) -> None:
    row = await import_connection(s, imp)
    imp.connection_id = None
    if row and not row.retained:
        await s.delete(row)


async def retain_import(s: AsyncSession, imp: Import) -> None:
    row = await import_connection(s, imp)
    if row is None or row.retained:
        return
    subject = (imp.options_json or {}).get("account_subject")
    secret = await credential(s, row.id) if subject else None
    if not secret:
        row.retained = True
        return
    retained = await save(s, provider=row.provider, owner_id=imp.user_id, subject=subject,
                          account=row.account, plan=row.plan, secret=secret)
    imp.connection_id = retained.id
    await s.delete(row)


async def lastfm_connection(s: AsyncSession) -> IntegrationConnection | None:
    return await s.scalar(select(IntegrationConnection).where(IntegrationConnection.provider == "lastfm"))


async def latest_owned(s: AsyncSession, owner_id: int, provider: str) -> IntegrationConnection | None:
    return await s.scalar(select(IntegrationConnection).where(
        IntegrationConnection.owner_user_id == owner_id, IntegrationConnection.provider == provider,
        IntegrationConnection.retained.is_(True),
    ).order_by(IntegrationConnection.id.desc()).limit(1))


async def shared_search_credential(provider: str) -> str | None:
    """Only public catalogue queries may use accounts shared by their owners."""
    if provider not in SHAREABLE:
        return None
    async with session_scope() as s:
        row = await s.scalar(select(IntegrationConnection).where(
            IntegrationConnection.provider == provider, IntegrationConnection.owner_user_id.is_not(None),
            IntegrationConnection.shared.is_(True), IntegrationConnection.retained.is_(True),
            IntegrationConnection.state == "connected",
        ).order_by(IntegrationConnection.checked_at.desc(), IntegrationConnection.id.desc()).limit(1))
        return await credential(s, row.id) if row else None


async def lastfm_key() -> str | None:
    async with session_scope() as s:
        row = await lastfm_connection(s)
        return await credential(s, row.id) if row else None


async def move_lastfm_from_env(key: str) -> None:
    """One-time transfer. Disconnecting Last.fm must not resurrect an old environment key."""
    async with session_scope() as s:
        marker = await s.execute(insert(AppSetting).values(key="lastfm_key_moved").on_conflict_do_nothing())
        if not marker.rowcount:
            return
        if key.strip() and await lastfm_connection(s) is None:
            row = await save(s, provider="lastfm", owner_id=None, secret=key.strip())
            row.connected_at = row.checked_at = None


async def remove(s: AsyncSession, connection: IntegrationConnection) -> None:
    await s.execute(delete(IntegrationConnection).where(IntegrationConnection.id == connection.id))
