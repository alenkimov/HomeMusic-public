"""`python -m app.backup restore [--day ГГГГ-ММ-ДД] [--yes]` (app/backup/restore.py)."""

import sys

from app.backup.restore import main

main(sys.argv[1:])
