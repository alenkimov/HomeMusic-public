"""A pool running low names who holds its connections."""

import logging

from app import db
from app.jobs.downloads import update_item


async def test_the_log_names_the_code_that_holds_a_connection(hub_env, monkeypatch, caplog):
    db.init_engine()
    monkeypatch.setattr(db, "POOL_REPORT_AT", 1)  # any checkout reports
    monkeypatch.setattr(db, "_reported_at", 0.0)
    try:
        with caplog.at_level(logging.WARNING, logger="app.db"):
            assert await update_item(10**9, status="queued") is None
        assert not db._holders  # every connection came back
    finally:
        await db.dispose()
    report = next(r.getMessage() for r in caplog.records if "pool running low" in r.getMessage())
    assert "downloads:" in report and "update_item" in report
