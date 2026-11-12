"""SQLite schema of the Hub (PLAN.md section 7)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, column_property, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    nd_username: Mapped[str] = mapped_column(String(128), unique=True)
    nd_password_enc: Mapped[str] = mapped_column(Text)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # Google account linked to this user ("sub" of its ID token): sign-in with Google in the app
    google_sub: Mapped[str | None] = mapped_column(String(255), unique=True)
    google_email: Mapped[str | None] = mapped_column(String(320))
    # the confirmed email the account registered with (lower case): sign-in by it, too
    email: Mapped[str | None] = mapped_column(String(320), unique=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime)
    # the user's own Telegram bot (app/telegram.py): its key (Fernet) and the private chat it sends tracks to
    # the user's ListenBrainz account, if they gave one (app/listenbrainz.py): the token (Fernet), the name it is
    # of, and the time of the last listen sent there
    lb_token_enc: Mapped[str | None] = mapped_column(Text)
    lb_user: Mapped[str | None] = mapped_column(String(128))
    lb_sent_until: Mapped[datetime | None] = mapped_column(DateTime)
    tg_bot_token_enc: Mapped[str | None] = mapped_column(Text)
    tg_bot_username: Mapped[str | None] = mapped_column(String(64))
    tg_chat_id: Mapped[str | None] = mapped_column(String(32))
    tg_chat_name: Mapped[str | None] = mapped_column(String(128))
    tg_with_lyrics: Mapped[bool] = mapped_column(Boolean, default=False)  # send the track's text with it by default


class SearchHistory(Base):
    """The account's recent queries, shared by browsers and the embedded app."""

    __tablename__ = "search_history"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    query_key: Mapped[str] = mapped_column(String(1024), primary_key=True)
    query: Mapped[str] = mapped_column(String(512))
    searched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AssistantDailyUsage(Base):
    """Persistent message allowance, independent of saved chats and LLM token usage."""

    __tablename__ = "assistant_daily_usage"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    messages: Mapped[int] = mapped_column(Integer)


class Invite(Base):
    """One-time registration code the admin hands to a person (FR-1: accounts only by invitation)."""

    __tablename__ = "invites"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)  # normalized: upper case, no dashes
    note: Mapped[str] = mapped_column(String(128), default="")  # who it is for
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime)
    used_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class EmailCode(Base):
    """A code sent to an email for registration without an invite: whoever reads the letter owns the address."""

    __tablename__ = "email_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), index=True)  # lower case
    code_hash: Mapped[str] = mapped_column(String(64))  # HMAC of the email and the code
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    attempts: Mapped[int] = mapped_column(Integer, default=0)  # wrong codes typed
    used_at: Mapped[datetime | None] = mapped_column(DateTime)


class AiUsage(Base):
    """Every paid AI call of a user (chat turn, screenshot/link/text recognition): the admin's spending report."""

    __tablename__ = "ai_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # chat/images/link/text
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256 of the cookie token
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class Album(Base):
    """Album as the Hub manages it. `uid` is written into files as HOMEMUSIC_ALBUM_ID (Navidrome album PID)."""

    __tablename__ = "albums"

    id: Mapped[int] = mapped_column(primary_key=True)
    uid: Mapped[str] = mapped_column(String(36), unique=True)
    title: Mapped[str] = mapped_column(String(512))
    album_artist: Mapped[str] = mapped_column(String(512))
    year: Mapped[int | None] = mapped_column(Integer)
    release_type: Mapped[str | None] = mapped_column(String(32))  # album/single/ep/compilation
    deezer_album_id: Mapped[str | None] = mapped_column(String(32), index=True)
    upc: Mapped[str | None] = mapped_column(String(32))
    genre: Mapped[str | None] = mapped_column(String(128))
    cover_path: Mapped[str | None] = mapped_column(String(1024))  # relative to library/, cover.jpg in album dir
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Track(Base):
    """A song the Hub knows (ARCHITECTURE.md): its names and length. Nothing here is about a file — a track has
    none, one or several (`TrackFile`), and the one that plays is the newest."""

    __tablename__ = "tracks"
    __table_args__ = (Index("ix_tracks_fingerprint", "fingerprint"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    album_id: Mapped[int | None] = mapped_column(ForeignKey("albums.id", ondelete="SET NULL"), index=True)
    artist: Mapped[str] = mapped_column(String(512))
    title: Mapped[str] = mapped_column(String(512))
    album: Mapped[str | None] = mapped_column(String(512))
    album_artist: Mapped[str | None] = mapped_column(String(512))
    track_no: Mapped[int | None] = mapped_column(Integer)
    disc_no: Mapped[int | None] = mapped_column(Integer)
    year: Mapped[int | None] = mapped_column(Integer)
    genre: Mapped[str | None] = mapped_column(String(128))
    duration_s: Mapped[int | None] = mapped_column(Integer)
    isrc: Mapped[str | None] = mapped_column(String(16), index=True)
    # what the catalog calls the song ("dz:3135556"): one track per ref. None — the catalog does not know it
    ref: Mapped[str | None] = mapped_column(String(64), unique=True)
    explicit: Mapped[bool | None] = mapped_column(Boolean)
    fingerprint: Mapped[str] = mapped_column(String(1024))  # normalized "artist|title"
    # what the catalog said of the song when the track was made (a `CatalogTrack`): its cover, its album and
    # artists there. A track without a file is drawn from this; None — the catalog does not know the song
    catalog: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # its files: given with a new track they are saved with it. Never read through here (a query names them)
    files: Mapped[list[TrackFile]] = relationship(lazy="raise", passive_deletes=True, cascade="save-update, merge")


class TrackFile(Base):
    """A file of the library that plays a track. `uid` is written into the file as HOMEMUSIC_TRACK_ID (Navidrome's
    track PID); `nd_song_id` — the song Navidrome made of the file, known after its scan."""

    __tablename__ = "track_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"), index=True)
    uid: Mapped[str] = mapped_column(String(36), unique=True)
    path: Mapped[str] = mapped_column(String(1024), unique=True)  # relative to library/, forward slashes
    nd_song_id: Mapped[str | None] = mapped_column(String(64), index=True)
    bitrate_kbps: Mapped[int | None] = mapped_column(Integer)
    codec: Mapped[str | None] = mapped_column(String(16))
    source: Mapped[str | None] = mapped_column(String(32))  # hitmoz/hitmos_fm/ytdlp/upload/manual
    source_url: Mapped[str | None] = mapped_column(String(2048))
    source_upload_date: Mapped[date | None] = mapped_column(Date)
    acquired_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    warnings: Mapped[list[Any]] = mapped_column(JSON, default=list)
    added_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


# What plays a track, read with the track: the Navidrome song and the path of its newest file (none: the track has
# no file, or Navidrome has not scanned it yet). Read-only — a file is changed through its own row.
Track.nd_song_id = column_property(
    select(TrackFile.nd_song_id).where(TrackFile.track_id == Track.id, TrackFile.nd_song_id.is_not(None))
    .order_by(TrackFile.id.desc()).limit(1).correlate_except(TrackFile).scalar_subquery())
Track.path = column_property(
    select(TrackFile.path).where(TrackFile.track_id == Track.id)
    .order_by(TrackFile.id.desc()).limit(1).correlate_except(TrackFile).scalar_subquery())


class CatalogCache(Base):
    __tablename__ = "catalog_cache"

    key: Mapped[str] = mapped_column(String(512), primary_key=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ttl_s: Mapped[int] = mapped_column(Integer)


class DownloadBatch(Base):
    __tablename__ = "download_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    origin: Mapped[str] = mapped_column(String(16))  # search/catalog/assistant
    title: Mapped[str | None] = mapped_column(String(512))
    target_playlist_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


ITEM_STATUSES = (
    "queued", "searching", "downloading", "tagging", "importing", "done", "failed", "needs_review", "cancelled",
)


class DownloadItem(Base):
    __tablename__ = "download_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("download_batches.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    query_json: Mapped[dict[str, Any]] = mapped_column(JSON)  # TrackQuery + catalog refs (deezer ids, album info)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    progress: Mapped[float] = mapped_column(Float, default=0)
    provider: Mapped[str | None] = mapped_column(String(32))
    chosen_candidate_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    candidates_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    warnings: Mapped[list[Any]] = mapped_column(JSON, default=list)
    track_id: Mapped[int | None] = mapped_column(ForeignKey("tracks.id", ondelete="SET NULL"))
    force: Mapped[bool] = mapped_column(Boolean, default=False)  # explicit re-download (new file next to locked one)
    # taken off the «Загрузки» list by the user; the row stays: a playlist's order, an import and a mix lean on it
    hidden: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_pick", "status", "run_after"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued/running/done/failed
    # higher first; imports run below 0 (`queue.PRIORITY_IMPORT`) and never take every worker
    priority: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    run_after: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    locked_by: Mapped[str | None] = mapped_column(String(64))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Upload(Base):
    __tablename__ = "uploads"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    staging_path: Mapped[str] = mapped_column(String(1024))  # relative to staging/
    orig_name: Mapped[str] = mapped_column(String(512))
    size: Mapped[int] = mapped_column(Integer, default=0)
    probed_tags_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="uploaded")  # uploaded/imported/failed/discarded
    error: Mapped[str | None] = mapped_column(Text)
    track_id: Mapped[int | None] = mapped_column(ForeignKey("tracks.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AssistantTask(Base):
    __tablename__ = "assistant_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    input_kind: Mapped[str] = mapped_column(String(16))  # images/link/text
    input_ref: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # image paths / url / text
    extraction_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    resolution_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    # queued/extracting/resolving/awaiting_confirmation/confirmed/done/failed
    confirm_token: Mapped[str | None] = mapped_column(String(64))
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("download_batches.id", ondelete="SET NULL"))
    error: Mapped[str | None] = mapped_column(Text)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AssistantChat(Base):
    """A saved conversation with the assistant: the messages as the PWA shows them (no images, only their count)."""

    __tablename__ = "assistant_chats"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    # [{"role", "content", "images"?, "cost"?, "done"?, "proposal"?: {task_id, token, summary, actions, state}}]
    messages: Mapped[list[Any]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Import(Base):
    """Moving a user's collection over from a music service (Yandex Music now; Spotify, YouTube Music later)."""

    __tablename__ = "imports"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(16))  # yandex
    method: Mapped[str] = mapped_column(String(16))  # device/token/profile
    # auth/fetching/preview/running/done/failed/cancelled
    status: Mapped[str] = mapped_column(String(16), default="fetching")
    mode: Mapped[str] = mapped_column(String(16), default="economy")  # economy/full
    account: Mapped[str | None] = mapped_column(String(256))  # login or profile shown to the user
    token_enc: Mapped[str | None] = mapped_column(Text)  # service token (Fernet)
    keep_token: Mapped[bool] = mapped_column(Boolean, default=False)  # kept after the import for «Обновить»
    auth_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # sign-in in progress (device code)
    options_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # download_albums...
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class ImportCollection(Base):
    """«Мне нравится», a playlist, liked albums / artists, dislikes of an import."""

    __tablename__ = "import_collections"

    id: Mapped[int] = mapped_column(primary_key=True)
    import_id: Mapped[int] = mapped_column(ForeignKey("imports.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # likes/playlist/albums/artists/dislikes
    external_id: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(512))
    position: Mapped[int] = mapped_column(Integer, default=0)
    selected: Mapped[bool] = mapped_column(Boolean, default=True)
    cover_url: Mapped[str | None] = mapped_column(String(1024))
    nd_playlist_id: Mapped[str | None] = mapped_column(String(64))  # 1.x: the Navidrome playlist it made
    nd_song_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)  # 1.x
    # a playlist of the service: the Hub's playlist made of it, and the tracks it was last given, in order
    playlist_id: Mapped[int | None] = mapped_column(ForeignKey("playlists.id", ondelete="SET NULL"))
    given: Mapped[list[Any]] = mapped_column(JSON, default=list)


class ImportItem(Base):
    """A track (or an album / artist) of an import collection, in the service's order."""

    __tablename__ = "import_items"
    __table_args__ = (Index("ix_import_items_ref", "ref"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    import_id: Mapped[int] = mapped_column(ForeignKey("imports.id", ondelete="CASCADE"), index=True)
    collection_id: Mapped[int] = mapped_column(ForeignKey("import_collections.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    external_id: Mapped[str] = mapped_column(String(128))
    raw_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # artist, title, album, duration_s...
    ref: Mapped[str | None] = mapped_column(String(64))  # catalog track / album / artist
    match: Mapped[str | None] = mapped_column(String(16))  # owned/exact/probable/not_found
    score: Mapped[float | None] = mapped_column(Float)
    # pending/waiting/queued/done/failed/not_found/skipped
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    track_id: Mapped[int | None] = mapped_column(ForeignKey("tracks.id", ondelete="SET NULL"))
    download_item_id: Mapped[int | None] = mapped_column(ForeignKey("download_items.id", ondelete="SET NULL"),
                                                         index=True)


class SourceMap(Base):
    """What an id of a music service turned out to be in the catalog: shared by every import of every user."""

    __tablename__ = "source_map"

    source: Mapped[str] = mapped_column(String(16), primary_key=True)  # yandex
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)  # track/album/artist
    external_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    ref: Mapped[str | None] = mapped_column(String(64))
    match: Mapped[str] = mapped_column(String(16))  # exact/probable/not_found
    score: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class PlayEvent(Base):
    """One listening event of a user (MIXES.md 3.2): start/skip/finish of a track, ♥, «Не нравится». From the app,
    the PWA's own player, or Navidrome's scrobble history (`client` "navidrome")."""

    __tablename__ = "play_events"
    __table_args__ = (
        UniqueConstraint("user_id", "event_id", name="uq_play_events_user_event"),
        Index("ix_play_events_user_ts", "user_id", "ts"),
        Index("ix_play_events_song", "nd_song_id"),
        Index("ix_play_events_user_target_ts", "user_id", "target_type", "ts", "id"),
        Index("ix_play_events_playback", "user_id", "device_id", "playback_id"),
        Index("ix_play_events_user_launch", "user_id", "launch_id"),
        Index("ix_play_events_track", "track_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    event_id: Mapped[str] = mapped_column(String(64))  # the client's id: a resent event is not stored twice
    ts: Mapped[datetime] = mapped_column(DateTime)  # UTC
    kind: Mapped[str] = mapped_column(String(16))  # start/skip/finish/like/unlike/dislike/undislike
    ref: Mapped[str | None] = mapped_column(String(64))  # catalog track
    nd_song_id: Mapped[str | None] = mapped_column(String(64))  # Navidrome song
    # the Hub's track the event is about (`app.plays.attach_tracks`): `ref` and `nd_song_id` are what the client
    # called it then — a song's id changes with its file, the track stays. None: a catalog track the Hub never kept
    track_id: Mapped[int | None] = mapped_column(ForeignKey("tracks.id", ondelete="SET NULL"))
    target_type: Mapped[str] = mapped_column(String(16), default="track", server_default="track")
    nd_album_id: Mapped[str | None] = mapped_column(String(64), index=True)
    nd_artist_id: Mapped[str | None] = mapped_column(String(64), index=True)
    target_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    reverts_event_id: Mapped[str | None] = mapped_column(String(64))
    mirror_pending: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    playback_id: Mapped[str | None] = mapped_column(String(64))
    device_id: Mapped[str | None] = mapped_column(String(64))
    listened_ms: Mapped[int | None] = mapped_column()
    track_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    superseded_scrobble: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    position_s: Mapped[float | None] = mapped_column(Float)
    duration_s: Mapped[float | None] = mapped_column(Float)
    context: Mapped[str | None] = mapped_column(String(128))  # mix:<id>, wave:<session>, album, playlist, search...
    client: Mapped[str] = mapped_column(String(32))  # app/<version>, pwa, navidrome
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # analytics (INSIGHTS.md 6.5): every event of one launched list shares `launch_id` ("wave:<sid>" for a wave);
    # `launch_origin` — where it was launched. The taste profile never reads them.
    launch_id: Mapped[str | None] = mapped_column(String(64))
    launch_origin: Mapped[str | None] = mapped_column(String(32))


class FavoriteSubject(Base):
    """Verified aliases and metadata, never the user's favorite state."""

    __tablename__ = "favorite_subjects"
    __table_args__ = (UniqueConstraint("target_type", "canonical_key", name="uq_favorite_subject_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    target_type: Mapped[str] = mapped_column(String(16))
    canonical_key: Mapped[str] = mapped_column(String(128))
    ref: Mapped[str | None] = mapped_column(String(64), index=True)
    nd_id: Mapped[str | None] = mapped_column(String(64), index=True)
    aliases: Mapped[list[Any]] = mapped_column(JSON, default=list)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    enriched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Dislike(Base):
    """«Не нравится»: a track (`ref` and/or `nd_song_id`) or an artist (`artist_ref`) the user doesn't want to hear."""

    __tablename__ = "dislikes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ref: Mapped[str | None] = mapped_column(String(64))
    nd_song_id: Mapped[str | None] = mapped_column(String(64))
    artist_ref: Mapped[str | None] = mapped_column(String(64))
    artist: Mapped[str | None] = mapped_column(String(512))  # for showing the list
    title: Mapped[str | None] = mapped_column(String(512))
    source: Mapped[str] = mapped_column(String(16))  # app/pwa/yandex_import
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class NavidromeSync(Base):
    """How far the Hub has read a user's Navidrome scrobble history (`app.plays.import_scrobbles`)."""

    __tablename__ = "navidrome_sync"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    last_scrobble_id: Mapped[int] = mapped_column(Integer, default=0)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime)
    favorites_synced_at: Mapped[datetime | None] = mapped_column(DateTime)
    favorites_bootstrapped_at: Mapped[datetime | None] = mapped_column(DateTime)



class MixSettings(Base):
    """A user's «Микс дня» settings (MIXES.md 6): mode, the hour of the build, the playlist, onboarding artists."""

    __tablename__ = "mix_settings"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), default="normal")  # normal/favorites/discover
    mix_hour: Mapped[int] = mapped_column(Integer, default=5)  # local time (`Settings.timezone`)
    nd_playlist_id: Mapped[str | None] = mapped_column(String(64))  # «Микс дня» in Navidrome: one id for good
    # onboarding: [{ref, name}] the user picked on «Что вы слушаете?» (a new user without history)
    artists_json: Mapped[list[Any]] = mapped_column(JSON, default=list)
    last_build_on: Mapped[date | None] = mapped_column(Date)  # local date of the last scheduled build attempt
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Mix(Base):
    """One «Микс дня» of a user: lives until the next one (MIXES.md 6)."""

    __tablename__ = "mixes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    title: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text, default="")
    mode: Mapped[str] = mapped_column(String(16))
    nd_playlist_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="active")  # active/closed
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("download_batches.id", ondelete="SET NULL"))
    stats_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # generators, timings (the report)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)


class MixItem(Base):
    """A song of a mix: one of the 50 (`state` "active") or the reserve that replaces one that did not download."""

    __tablename__ = "mix_items"
    __table_args__ = (Index("ix_mix_items_mix", "mix_id", "state", "position"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    mix_id: Mapped[int] = mapped_column(ForeignKey("mixes.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)  # order in the mix; the reserve after the 50, best first
    state: Mapped[str] = mapped_column(String(16), default="active")  # active/reserve/failed
    familiar: Mapped[bool] = mapped_column(Boolean, default=False)
    ref: Mapped[str | None] = mapped_column(String(64), index=True)  # catalog track
    nd_song_id: Mapped[str | None] = mapped_column(String(64), index=True)
    track_id: Mapped[int | None] = mapped_column(ForeignKey("tracks.id", ondelete="SET NULL"), index=True)
    generator: Mapped[str] = mapped_column(String(32))  # the strongest one: favorites/stash/ytm_radio/...
    score: Mapped[float] = mapped_column(Float, default=0)
    # the catalog track (`CatalogTrack`) and what scored it: generators, sim, popularity
    features_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    downloaded_for_mix: Mapped[bool] = mapped_column(Boolean, default=False)
    download_item_id: Mapped[int | None] = mapped_column(ForeignKey("download_items.id", ondelete="SET NULL"),
                                                         index=True)


class WaveSession(Base):
    """One «Моя волна» of a user (MIXES.md 7): the endless stream the Hub picks 5 tracks at a time. `state_json` —
    the pool of candidates, what was handed out and played, the session's skips and likes (`app.mixes.wave`)."""

    __tablename__ = "wave_sessions"
    __table_args__ = (Index("ix_wave_sessions_user_created", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    mode: Mapped[str] = mapped_column(String(16), default="normal")  # normal/favorites/discover, as a mix's
    seed_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # {ref|nd_song_id|artist_ref, title}: «по треку»
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("download_batches.id", ondelete="SET NULL"))
    state_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # analytics (INSIGHTS.md 6.2–6.3): where the wave was started, by what client, the raw request; a session the
    # app made again after a 404 names the old one (`origin` "restart"). Never read by the wave itself.
    origin: Mapped[str | None] = mapped_column(String(32))
    client: Mapped[str | None] = mapped_column(String(32))
    restart_of: Mapped[str | None] = mapped_column(String(32))
    taste_model: Mapped[str | None] = mapped_column(String(8))
    request_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    next_calls: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # an old session's heavy state (pool, queue, played) was replaced by a summary: the row itself is kept
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime)


class WaveItem(Base):
    """A track a wave handed out (INSIGHTS.md 6.4), once per session: analytics only, the wave never reads it."""

    __tablename__ = "wave_items"
    __table_args__ = (UniqueConstraint("session_id", "ref", name="uq_wave_items_session_ref"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("wave_sessions.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    first_handed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_handed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    handed_count: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    position: Mapped[int] = mapped_column(Integer)  # the order of first hand-outs in the session, from 0
    ref: Mapped[str] = mapped_column(String(64))
    nd_song_id: Mapped[str | None] = mapped_column(String(64))
    key: Mapped[str] = mapped_column(String(64))  # the vector key (nd:<song> or the ref)
    artist: Mapped[str] = mapped_column(String(256), default="", server_default="")
    title: Mapped[str] = mapped_column(String(256), default="", server_default="")
    generator: Mapped[str] = mapped_column(String(32))  # `wave.top_generator`
    gens: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # generator -> best position in its list
    familiar: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    score: Mapped[float] = mapped_column(Float, default=0, server_default="0")
    sim: Mapped[float | None] = mapped_column(Float)
    value: Mapped[float | None] = mapped_column(Float)  # `wave.value` the pick ranked it by (none: the seed)


class SmartSession(Base):
    """One run of the smart shuffle of a playlist or of «Любимое» (MIXES.md 8): the order of the own songs
    and the recommendations the Hub put between them. `state_json` — what the session needs to go on after a
    restart (`app.mixes.smart`); the rest is for the analytics."""

    __tablename__ = "smart_sessions"
    __table_args__ = (Index("ix_smart_sessions_user_context", "user_id", "context", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    context: Mapped[str] = mapped_column(String(80))  # "liked" or "playlist:<Navidrome id>"
    title: Mapped[str | None] = mapped_column(String(256))  # the playlist's name as the client sent it
    own_count: Mapped[int] = mapped_column(Integer, default=0)
    per_rec: Mapped[int] = mapped_column(Integer, default=3)  # own songs per recommendation
    first_id: Mapped[str | None] = mapped_column(String(80))  # the song it started from (tapped or playing)
    taste_model: Mapped[str | None] = mapped_column(String(8))
    origin: Mapped[str | None] = mapped_column(String(32))
    client: Mapped[str | None] = mapped_column(String(32))
    next_calls: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    state_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class SmartItem(Base):
    """A recommendation a smart shuffle handed out, once per session (analytics; `state` follows the user)."""

    __tablename__ = "smart_items"
    __table_args__ = (UniqueConstraint("session_id", "ref", name="uq_smart_items_session_ref"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("smart_sessions.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    handed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    position: Mapped[int] = mapped_column(Integer)  # the order of hand-outs in the session, from 0
    slot: Mapped[int] = mapped_column(Integer)  # which recommendation place of the list it took
    ref: Mapped[str] = mapped_column(String(64))
    nd_song_id: Mapped[str | None] = mapped_column(String(64))
    fingerprint: Mapped[str] = mapped_column(String(1024), default="", server_default="")
    artist: Mapped[str] = mapped_column(String(256), default="", server_default="")
    title: Mapped[str] = mapped_column(String(256), default="", server_default="")
    generator: Mapped[str] = mapped_column(String(32))
    gens: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    familiar: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    score: Mapped[float] = mapped_column(Float, default=0, server_default="0")
    sim: Mapped[float | None] = mapped_column(Float)  # closeness by sound to the playlist's center
    value: Mapped[float | None] = mapped_column(Float)
    state: Mapped[str] = mapped_column(String(12), default="handed", server_default="handed")  # handed/added/hidden


class SmartHidden(Base):
    """«Не рекомендовать в этом плейлисте»: a track the user hid from the recommendations of one context. Not the
    global «Не нравится» (`Dislike`): the track stays everywhere else."""

    __tablename__ = "smart_hidden"
    __table_args__ = (UniqueConstraint("user_id", "context", "fingerprint", name="uq_smart_hidden"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    context: Mapped[str] = mapped_column(String(80))
    fingerprint: Mapped[str] = mapped_column(String(1024))
    ref: Mapped[str | None] = mapped_column(String(64))
    nd_song_id: Mapped[str | None] = mapped_column(String(64))
    artist: Mapped[str] = mapped_column(String(256), default="", server_default="")
    title: Mapped[str] = mapped_column(String(256), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Lyrics(Base):
    """The lyrics of a library track (app/lyrics): kept here so they open at once and without the internet."""

    __tablename__ = "lyrics"

    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(8), index=True)  # word/synced/plain/none (looked for, not found)
    source: Mapped[str | None] = mapped_column(String(16))  # file/lrclib/netease/ovh
    # [{"t": ms, "text": str, "end"?: ms, "words"?: [{"t": ms, "text": str}]}] — a timed text
    lines_json: Mapped[list[Any] | None] = mapped_column(JSON)
    plain: Mapped[str | None] = mapped_column(Text)  # a text without timings
    search_text: Mapped[str | None] = mapped_column(Text)  # the whole text, case-folded: the search inside lyrics
    offset_ms: Mapped[int] = mapped_column(Integer, default=0)  # the user's shift of the timings
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)  # chosen by hand: the search leaves it alone
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class TrackLink(Base):
    """A library track's id on a music service (app/links.py): what «Поделиться ссылкой» is made of."""

    __tablename__ = "track_links"
    __table_args__ = (Index("ix_track_links_external", "service", "external_id"),)

    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"), primary_key=True)
    service: Mapped[str] = mapped_column(String(16), primary_key=True)  # spotify/yandex/ytm
    external_id: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class TgChat(Base):
    """A chat the user's Telegram bot can send to (app/telegram_chats.py): the private one, a group, a channel."""

    __tablename__ = "tg_chats"
    __table_args__ = (UniqueConstraint("user_id", "chat_id", name="uq_tg_chats_user_chat"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    chat_id: Mapped[str] = mapped_column(String(32))  # Telegram's id
    kind: Mapped[str] = mapped_column(String(16))  # private/group/supergroup/channel
    title: Mapped[str] = mapped_column(String(256))
    photo: Mapped[bytes | None] = mapped_column(LargeBinary)  # the chat's small picture (a JPEG)
    photo_key: Mapped[str | None] = mapped_column(String(64))  # Telegram's id of that picture: changes with it
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class PlaylistDate(Base):
    """When a track was added to a Navidrome playlist (app/playlist_dates.py): Navidrome keeps no such date."""

    __tablename__ = "playlist_dates"

    nd_playlist_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"), primary_key=True)
    added_at: Mapped[datetime] = mapped_column(DateTime)
    source: Mapped[str] = mapped_column(String(16))  # hub (added here) / import (the service's own date)


class LyricsTranslation(Base):
    """A track's lyrics in another language (app/lyrics/translations.py): made once, kept for everyone."""

    __tablename__ = "lyrics_translations"

    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"), primary_key=True)
    lang: Mapped[str] = mapped_column(String(8), primary_key=True)  # ru/en/...
    provider: Mapped[str | None] = mapped_column(String(16))  # llm/google/mymemory
    lines_json: Mapped[list[Any] | None] = mapped_column(JSON)  # line for line with the text it was made of
    source_sig: Mapped[str | None] = mapped_column(String(40))  # of that text: another text makes it stale
    edited_json: Mapped[list[Any] | None] = mapped_column(JSON)  # the lines a listener corrected by hand
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PlaylistCover(Base):
    """A picture the user gave a playlist (app/api/playlists.py): shown instead of the one Navidrome makes."""

    __tablename__ = "playlist_covers"

    nd_playlist_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    image: Mapped[bytes] = mapped_column(LargeBinary)  # a square JPEG
    key: Mapped[str] = mapped_column(String(16))  # changes with the picture: the page's cache follows it
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Tag(Base):
    """A tag tracks are filtered by (app/tags.py). `group`: lang / genre — everyone's, set by the Hub itself; own —
    a user's (`user_id`), set by hand."""

    __tablename__ = "tags"
    __table_args__ = (Index("ix_tags_group_user", "group", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    group: Mapped[str] = mapped_column(String(8))
    name: Mapped[str] = mapped_column(String(64))
    code: Mapped[str | None] = mapped_column(String(16))  # of a language (ja, en...), «instrumental»
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class TrackTag(Base):
    """A tag on a track. `auto`: the Hub set it (and takes it off when it no longer holds)."""

    __tablename__ = "track_tags"

    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True, index=True)
    auto: Mapped[bool] = mapped_column(Boolean, default=False)


class TagRemoved(Base):
    """A tag the Hub would set by itself, taken off this track by hand: it is not set again."""

    __tablename__ = "tags_removed"

    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True)


class TagSelection(Base):
    """«Подборка по тегам»: a saved filter — where to look (`scope`: liked / playlist:<id> / library) and which tags
    are wanted or left out. It holds no tracks: the list is made anew every time it is opened."""

    __tablename__ = "tag_selections"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    scope: Mapped[str] = mapped_column(String(80))
    filter_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # tag id / "untagged" -> yes / no
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ListenHistory(Base):
    """A listen brought in from another service's history («Статистика», app/stats.py): Spotify's «Extended streaming
    history». It is only counted — mixes and the taste profile never read it. `key` names the track: "sp:<id>" or
    "t:<artist|title>"; which library track it is, is told anew every time (a track downloaded later is found)."""

    __tablename__ = "listen_history"
    __table_args__ = (
        UniqueConstraint("user_id", "ts", "key", name="uq_listen_history"),
        Index("ix_listen_history_user_ts", "user_id", "ts"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    ts: Mapped[datetime] = mapped_column(DateTime)  # UTC, when the listen ended
    ms: Mapped[int] = mapped_column(Integer)  # listened
    key: Mapped[str] = mapped_column(String(200))
    spotify_id: Mapped[str | None] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(512))
    artist: Mapped[str] = mapped_column(String(512))
    album: Mapped[str | None] = mapped_column(String(512))
    source: Mapped[str] = mapped_column(String(16), default="spotify")


class Playlist(Base):
    """A playlist (ARCHITECTURE.md): the Hub's own, with tracks that may have no file yet. `kind`: "liked" —
    «Любимое», one per user; "user" — made by hand or by an import. What third-party apps see of it is a mirror in
    Navidrome (`nd_playlist_id`); `mirrored` — the songs the mirror was last given."""

    __tablename__ = "playlists"
    __table_args__ = (Index("ix_playlists_user", "user_id", "kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(8), default="user")
    name: Mapped[str] = mapped_column(String(200))
    nd_playlist_id: Mapped[str | None] = mapped_column(String(64), index=True)
    mirrored: Mapped[list[Any]] = mapped_column(JSON, default=list)
    nd_cover: Mapped[str | None] = mapped_column(String(128))  # the picture Navidrome makes of the mirror's albums
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PlaylistItem(Base):
    """A track's place in a playlist. A track may stand in a playlist more than once."""

    __tablename__ = "playlist_items"
    __table_args__ = (Index("ix_playlist_items_order", "playlist_id", "position"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    playlist_id: Mapped[int] = mapped_column(ForeignKey("playlists.id", ondelete="CASCADE"))
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DiscoveryIssue(Base):
    """One week's «Открытия недели» (app/mixes/discover.py): catalog tracks of artists the user has not heard.
    Issues are kept — an old week can be opened again; a track that was in one is not picked for another."""

    __tablename__ = "discovery_issues"
    __table_args__ = (UniqueConstraint("user_id", "week", name="uq_discovery_issues"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    week: Mapped[date] = mapped_column(Date)  # the Monday it is of, in the users' local time
    tracks_json: Mapped[list[Any]] = mapped_column(JSON, default=list)  # CatalogTrack dumps, in order
    stats_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ArtistRelease(Base):
    """A release of an artist the user follows (♥ on the artist's page) — the «Релизы» feed, app/releases.py."""

    __tablename__ = "artist_releases"
    __table_args__ = (
        UniqueConstraint("user_id", "album_ref", name="uq_artist_releases"),
        Index("ix_artist_releases_user_date", "user_id", "release_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    artist_ref: Mapped[str] = mapped_column(String(64))  # the followed artist it was found at
    artist: Mapped[str] = mapped_column(String(512))
    album_ref: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(512))
    record_type: Mapped[str] = mapped_column(String(16), default="album")  # album | single | ep | compilation
    release_date: Mapped[date] = mapped_column(Date)
    cover_url: Mapped[str | None] = mapped_column(String(512))
    nb_tracks: Mapped[int | None] = mapped_column(Integer)
    found_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    seen_at: Mapped[datetime | None] = mapped_column(DateTime)  # the user has opened the feed since


class ArtistReleaseCheck(Base):
    """When the releases of a followed artist were last looked up."""

    __tablename__ = "artist_release_checks"
    __table_args__ = (UniqueConstraint("user_id", "artist_ref", name="uq_artist_release_checks"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    artist_ref: Mapped[str] = mapped_column(String(64))
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AppSetting(Base):
    """A setting of the whole server that the admin changes in the app (the order of lyrics sources)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
