"""The Hub's working folder off the music folder: what was in the old one comes along once, caches do not."""

from __future__ import annotations


def test_the_old_working_folder_moves_once_and_its_caches_go(tmp_path, monkeypatch):
    from app import config, staging

    music, work = tmp_path / "music", tmp_path / "work"
    old = music / "staging"
    (old / "listen" / "abc").mkdir(parents=True)
    (old / "listen" / "abc" / "stream").write_bytes(b"x" * 10)
    (old / "upgrade" / "7").mkdir(parents=True)
    (old / "upgrade" / "7" / "better.flac").write_bytes(b"flac")
    (old / "uploads").mkdir()
    (old / "uploads" / "a.mp3").write_bytes(b"mp3")
    (work / "uploads").mkdir(parents=True)
    (work / "uploads" / "b.mp3").write_bytes(b"new")
    monkeypatch.setenv("MUSIC_ROOT", str(music))
    monkeypatch.setenv("STAGING_ROOT", str(work))
    config.get_settings.cache_clear()
    try:
        assert config.get_settings().staging_dir == work
        staging.move_old()
        assert (work / "upgrade" / "7" / "better.flac").read_bytes() == b"flac"  # waits for a decision: kept
        assert sorted(p.name for p in (work / "uploads").iterdir()) == ["a.mp3", "b.mp3"]  # merged into what is there
        assert not (work / "listen").exists()  # a cache: filled again by itself
        assert not old.exists()
        staging.move_old()  # nothing left to move
    finally:
        config.get_settings.cache_clear()


def test_without_a_working_folder_of_its_own_nothing_moves(tmp_path, monkeypatch):
    from app import config, staging

    (tmp_path / "staging" / "uploads").mkdir(parents=True)
    monkeypatch.setenv("MUSIC_ROOT", str(tmp_path))
    monkeypatch.delenv("STAGING_ROOT", raising=False)
    config.get_settings.cache_clear()
    try:
        assert config.get_settings().staging_dir == tmp_path / "staging"
        staging.move_old()
        assert (tmp_path / "staging" / "uploads").is_dir()
    finally:
        config.get_settings.cache_clear()
