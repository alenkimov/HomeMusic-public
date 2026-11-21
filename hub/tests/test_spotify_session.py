from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.catalog import spotify_collection_pb2 as wire
from app.catalog import spotify_session

CATALOG_URI = "spotify:track:0123456789abcdefghijkl"
LOCAL_URI = "spotify:local:Artist:Album:Local%20song:210"


def _page(*items: wire.CollectionItem, token: str = "") -> MagicMock:
    response = MagicMock()
    response.__enter__.return_value = response
    response.content = wire.PageResponse(items=items, next_page_token=token).SerializeToString()
    return response


def test_saved_collection_uses_all_pages_for_check_and_import(monkeypatch):
    def make_pages():
        return [
            _page(
                wire.CollectionItem(uri=CATALOG_URI, added_at=20),
                wire.CollectionItem(uri="spotify:album:0123456789abcdefghijkl", added_at=40),
                wire.CollectionItem(uri="spotify:local:Artist:Album:Removed:120", is_removed=True),
                token="page-two",
            ),
            _page(
                wire.CollectionItem(uri=LOCAL_URI, added_at=30),
                wire.CollectionItem(uri=CATALOG_URI, added_at=20),
            ),
        ]

    api = MagicMock()
    session = SimpleNamespace(api=lambda: api, username=lambda: "listener", get_user_attribute=lambda _: "free")
    tracks = MagicMock(return_value=[{"track": {"uri": LOCAL_URI}}])
    monkeypatch.setattr(spotify_session, "_tracks", tracks)

    api.send.side_effect = make_pages()
    checked = spotify_session._perform(session, "check", {})
    assert checked["liked_read"] == 2
    tracks.assert_not_called()

    api.send.reset_mock()
    api.send.side_effect = make_pages()
    result = spotify_session._perform(session, "collection", {})
    assert result["expected"] == 2
    assert result["tracks"] == tracks.return_value
    tracks.assert_called_once_with(api, [
        {"uri": LOCAL_URI, "metadata": {"added_at": 30}},
        {"uri": CATALOG_URI, "metadata": {"added_at": 20}},
    ])
    requests = [wire.PageRequest.FromString(call.args[3]) for call in api.send.call_args_list]
    assert [request.pagination_token for request in requests] == ["", "page-two"]
    for call, request in zip(api.send.call_args_list, requests, strict=True):
        assert call.args[:2] == ("POST", "/collection/v2/paging")
        assert (request.username, request.set, request.limit) == ("listener", "collection", 300)


def test_saved_collection_rejects_repeated_page_token():
    api = MagicMock()
    api.send.side_effect = [_page(token="repeated"), _page(token="repeated")]

    with pytest.raises(spotify_session.SessionError):
        spotify_session._liked_rows(api, "listener")

    assert api.send.call_count == 2
