"""scribe-cli.exe — the same app for a terminal, e.g. `scribe-cli record --title "Sync"`."""

import sys

from scribe.main import main

if len(sys.argv) == 1:
    sys.argv.append("--help")
main()
