"""Backups on Google Drive: what is the Hub's alone leaves the server every night.

What goes (`service.run`): the database — history, likes, playlists, tags, lyrics, everybody's edits — as the night's
dump (`housekeeping.backup_hub_db`), the files a listener uploaded by hand, the covers of albums set by hand, and
`config.yaml`. 7 daily copies of the database are kept and 4 weekly ones; a file is sent once. What does not go:
the downloaded files (the database knows every song: after a restore the Hub downloads them again), and `.env` —
its `HUB_SECRET_KEY` is what the secrets in the database are encrypted with (the listeners' passwords, the
services' tokens), so it never lies next to them. The admin keeps a copy of `.env` themselves.

On Drive: a folder «HomeMusic backups», with `database/` (hub-YYYY-MM-DD.dump), `files/` and config.yaml. The Hub
sees nothing else of the Drive (`drive.file`).

A restore (`restore`, `python -m app.backup restore`, scripts/restore-server.ps1) is the other way round on a new
server: the database of a chosen day, the uploaded files and the covers put back, the downloaded songs queued to be
downloaded again, the listeners' Navidrome accounts made again.
"""
