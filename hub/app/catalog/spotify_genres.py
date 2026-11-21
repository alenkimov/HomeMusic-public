"""Spotify Web Player's audio-descriptor extension, using the existing guest client."""

from __future__ import annotations

from collections.abc import Iterator
from io import BytesIO

from spotapi import Catalogue

URL = "https://spclient.wg.spotify.com/extended-metadata/v0/extended-metadata"
AUDIO_DESCRIPTORS = 6
GENRE = 1


def _varint(value: int) -> bytes:
    out = bytearray()
    while value >= 128:
        out.append((value & 127) | 128)
        value >>= 7
    out.append(value)
    return bytes(out)


def _blob(field: int, value: bytes) -> bytes:
    return _varint((field << 3) | 2) + _varint(len(value)) + value


def _number(stream: BytesIO) -> int:
    value = 0
    for shift in range(0, 70, 7):
        octet = stream.read(1)
        if not octet:
            break
        value |= (octet[0] & 127) << shift
        if octet[0] < 128:
            return value
    raise ValueError("Incomplete Spotify protobuf varint")


def _fields(data: bytes) -> Iterator[tuple[int, int | bytes]]:
    stream = BytesIO(data)
    while stream.tell() < len(data):
        tag = _number(stream)
        wire = tag & 7
        if wire == 0:
            yield tag >> 3, _number(stream)
            continue
        if wire not in (1, 2, 5):
            raise ValueError("Unexpected Spotify protobuf wire type")
        size = _number(stream) if wire == 2 else (8 if wire == 1 else 4)
        value = stream.read(size)
        if len(value) != size:
            raise ValueError("Incomplete Spotify protobuf field")
        yield tag >> 3, value


def _blobs(data: bytes, field: int) -> Iterator[bytes]:
    for key, value in _fields(data):
        if key == field and isinstance(value, bytes):
            yield value


def _descriptor_types(value: int | bytes) -> Iterator[int]:
    if isinstance(value, int):
        yield value
    else:
        stream = BytesIO(value)
        while stream.tell() < len(value):
            yield _number(stream)


def _genres(payload: bytes) -> list[str]:
    names: dict[str, str] = {}
    for row in _blobs(payload, 1):
        fields = list(_fields(row))
        if not any(GENRE in _descriptor_types(value) for key, value in fields if key == 3):
            continue
        labels = {key: value.decode().strip() for key, value in fields if key in (1, 5) and isinstance(value, bytes)}
        name = labels.get(5) or labels.get(1)
        if name:
            names.setdefault(name.casefold(), name)
    return list(names.values())


def decode(data: bytes, uris: dict[str, str]) -> dict[str, list[str]]:
    """Absent IDs and provider errors are distinct from a successful empty response."""
    out: dict[str, list[str]] = {}
    for array in _blobs(data, 2):
        if (2, AUDIO_DESCRIPTORS) not in _fields(array):
            continue
        for row in _blobs(array, 3):
            uri = next(_blobs(row, 2), b"").decode()
            if uri not in uris:
                continue
            header = dict(_fields(next(_blobs(row, 1), b"")))
            if header.get(1) == 404:
                out[uris[uri]] = []
            elif header.get(1) == 200:
                value = next(_blobs(row, 3), b"")
                payload = next(_blobs(value, 2), None)
                if payload is not None:
                    out[uris[uri]] = _genres(payload)
    return out


async def fetch(catalog: Catalogue, ids: list[str]) -> dict[str, list[str]]:
    uris = {f"spotify:track:{id_}": id_ for id_ in ids}
    extension = _blob(2, b"\x08" + _varint(AUDIO_DESCRIPTORS))
    body = b"".join(_blob(2, _blob(1, uri.encode()) + extension) for uri in uris)
    async with catalog.queries._get_pool().acquire() as client:
        response = await client.http.post(
            URL, authorized=True, max_attempts=1, retry_safe=False, body=body,
            headers={"content-type": "application/x-protobuf", "accept": "application/x-protobuf"},
        )
    return decode(response.body, uris)
