"""
Run Minutara's test suites, one process each (they patch modules and start
servers). The audio hardware, Whisper and the cloud services are faked
(fakes.py, fake_cloud.py), so this runs on any OS, no microphone needed.

    python tests/run_all.py            # everything
    python tests/run_all.py --no-ui    # skip the browser tests (no Playwright needed)
    python tests/run_all.py export     # only suites whose name contains "export"

The browser tests need Playwright with Chromium:
    pip install playwright && python -m playwright install chromium
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    suites = sorted(HERE.glob("test_*.py"))
    if "--no-ui" not in sys.argv:
        suites += sorted(HERE.glob("ui_*.py"))
    if args:
        suites = [s for s in suites if any(a in s.stem for a in args)]
    failed = []
    for s in suites:
        t0 = time.monotonic()
        r = subprocess.run([sys.executable, str(s)], cwd=HERE, capture_output=True, text=True,
                           timeout=900)
        out = r.stdout + r.stderr
        bad = [ln for ln in out.splitlines() if ln.startswith("FAIL")]
        ok = r.returncode == 0 and not bad
        print(f"{'ok  ' if ok else 'FAIL'}  {s.name:<26} {time.monotonic() - t0:5.1f}s")
        if not ok:
            failed.append(s.name)
            print("\n".join(bad) or out[-3000:])
    print(f"\n{len(suites) - len(failed)}/{len(suites)} suites passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
