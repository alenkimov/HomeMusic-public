from app import updates


def test_versions_are_compared_by_their_numbers():
    assert updates.key("2026.11.23") > updates.key("2026.10.26")
    assert updates.key("v2.0.10") > updates.key("2.0.9")
    assert updates.key("2026.11.9") < updates.key("2026.11.10")


def test_what_is_behind_is_told_by_what_is_installed_now(monkeypatch):
    have = {"yt-dlp": "2026.11.23", "spotapi": "2.1.0", "yt-dlp-ejs": "0.8.0", "bgutil-ytdlp-pot-provider": None}
    monkeypatch.setattr(updates, "installed", lambda p: have.get(p))
    monkeypatch.setattr("app.version.version", lambda: "2026.11.9")
    state = {"checked_at": "2026-11-27T00:00:00+00:00", "items": [
        # written down before an update: yt-dlp was behind then, it is not now
        {"name": "yt-dlp", "installed": "2026.10.26", "latest": "2026.11.23", "why": "…"},
        {"name": "spotapi", "installed": "2.1.0", "latest": "2.1.3", "why": "…"},
        {"name": "yt-dlp-ejs", "installed": "0.8.0", "latest": None, "why": "…"},  # not asked
        {"name": "bgutil-ytdlp-pot-provider", "installed": None, "latest": "2.0.2", "why": "…"},  # not installed
        {"name": "HomeMusic", "installed": "2026.11.9", "latest": "2026.11.10", "why": "…"},
    ]}
    assert [(i["name"], i["installed"], i["latest"]) for i in updates.outdated(state)] == [
        ("spotapi", "2.1.0", "2.1.3"), ("HomeMusic", "2026.11.9", "2026.11.10")]
    assert updates.outdated(None) == []
