#!/usr/bin/env python3
"""Cross-platform launcher for UniTS-Web."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def default_units_root(project_root: Path) -> Path:
    env_root = os.environ.get("UNITS_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    return (project_root.parent / "UniTS").resolve()


def main() -> int:
    project_root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Start UniTS-Web")
    parser.add_argument("--host", default=os.environ.get("UNITS_WEB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("UNITS_WEB_PORT", "7860")))
    parser.add_argument("--units-root", default=str(default_units_root(project_root)))
    args = parser.parse_args()

    app = project_root / "app.py"
    cmd = [
        sys.executable,
        str(app),
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--units-root",
        str(Path(args.units_root).expanduser().resolve()),
    ]
    print("Starting UniTS-Web")
    print(f"URL: http://{args.host}:{args.port}")
    print(f"UniTS root: {Path(args.units_root).expanduser().resolve()}")
    return subprocess.call(cmd, cwd=str(project_root))


if __name__ == "__main__":
    raise SystemExit(main())
