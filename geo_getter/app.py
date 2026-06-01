from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Launch GEOGetter desktop GUI")
    parser.add_argument("--smoke-test", action="store_true", help="initialize the PowerShell WinForms GUI and exit")
    args = parser.parse_args(argv)

    script = Path(__file__).resolve().parent.parent / "GEOGetter.ps1"
    if not script.exists():
        print(f"GUI script not found: {script}", file=sys.stderr)
        return 1

    command = [
        "powershell",
        "-NoProfile",
        "-STA",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
    ]
    if args.smoke_test:
        command.append("-SmokeTest")
    completed = subprocess.run(command)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
