"""Image metadata and versioned addresses for artwork saved by the Hub."""

import hashlib
from io import BytesIO

from PIL import Image

from app.models import Album


def cover_info(image: bytes | None) -> dict | None:
    """The same metadata used to compare pictures during file import and cover selection."""
    if not image:
        return None
    out = {"hash": hashlib.md5(image).hexdigest()[:12], "size": len(image)}
    try:
        with Image.open(BytesIO(image)) as im:
            out.update(w=im.width, h=im.height, format=im.format)
    except (OSError, ValueError, Image.DecompressionBombError):
        pass
    return out


def album_cover_url(album: Album) -> str | None:
    if not album.cover_path:
        return None
    return f"/api/library/albums/{album.id}/cover?v={album.updated_at:%Y%m%d%H%M%S%f}"
