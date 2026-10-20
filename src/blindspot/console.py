"""UTF-8 console output on every platform (AGENTS §6: Windows crashed on non-Latin text in Phase 1).

The one helper for CLIs and scripts: `utf8_console()` reconfigures stdout and stderr once. A stream
that can't be reconfigured (a test's StringIO, some IDE consoles) is left alone.
"""

from __future__ import annotations

import sys


def utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")
