#!/usr/bin/env python3
"""Evening snapshot for the KOSPI morning briefing (cron --no-agent job, weekdays 16:10 and 20:10 KST).

Program trading, breadth, sector flows and theme moves are only served for the current session: by the
08:00 run the portals have reset them for the new day. This saves them to
$KOSPI_SNAPSHOT_DIR (default <forecast dir>/snapshots/<session>.json) for kospi_morning.py to read.
Silent on success, so the cron job delivers nothing; prints one warning line if the 20:10 run is incomplete.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kospi_morning  # noqa: E402

kospi_morning.snapshot()
