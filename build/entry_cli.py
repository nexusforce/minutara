"""minutara-cli.exe — the same app for a terminal, e.g. `minutara-cli record --title "Sync"`."""

import sys

from minutara.main import main

if len(sys.argv) == 1:
    sys.argv.append("--help")
main()
