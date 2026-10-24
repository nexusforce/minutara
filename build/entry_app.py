"""Minutara.exe — double-click to open the app.

Built without a console window. With no arguments it starts the engine and
opens the app window; with arguments it runs that command (its output goes
to minutara.log, since there's no console — use minutara-cli.exe for a terminal).
"""

import sys

from minutara.main import main

if len(sys.argv) == 1:
    sys.argv += ["serve", "--open"]
main()
