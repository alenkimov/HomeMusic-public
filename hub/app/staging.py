"""The Hub's working folder moved off the music folder (`Settings.staging_root`): what was in the old one comes along
once, at the start. What is worth keeping moves — uploads, files waiting for a decision (a better file found,
`app.library.upgrade`), a backup being restored; what is a cache and fills again by itself goes — the copies of what
was listened to, the light copies, the covers."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from app.config import get_settings

log = logging.getLogger(__name__)

CACHES = {"listen", "light", "covers"}  # filled again by themselves


def _merge(src: Path, dest: Path) -> None:
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        return
    if src.is_dir() and dest.is_dir():
        for child in src.iterdir():
            _merge(child, dest / child.name)
        shutil.rmtree(src, ignore_errors=True)
    # a file there already: the one in the new place stays


def move_old() -> None:
    """Called at the start (in a thread): the old MUSIC_ROOT/staging into the new working folder, once."""
    s = get_settings()
    old = s.music_root / "staging"
    if s.staging_root is None or not old.is_dir() or old.resolve() == s.staging_dir.resolve():
        return
    s.staging_dir.mkdir(parents=True, exist_ok=True)
    moved = dropped = 0
    for entry in list(old.iterdir()):
        try:
            if entry.name in CACHES:
                shutil.rmtree(entry, ignore_errors=True) if entry.is_dir() else entry.unlink(missing_ok=True)
                dropped += 1
            else:
                _merge(entry, s.staging_dir / entry.name)
                moved += 1
        except OSError as e:
            log.warning("staging: %s not moved: %r", entry, e)
    try:
        old.rmdir()
    except OSError:
        pass  # something did not move: it stays, and is tried again at the next start
    log.info("staging moved to %s: %d kept, %d caches dropped", s.staging_dir, moved, dropped)
