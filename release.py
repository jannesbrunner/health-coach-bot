#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# ///
"""
Release-Skript: Patch-Version bumpen, Docker-Image bauen und pushen.
Aufruf: uv run release.py
"""

import re
import subprocess
import sys
from pathlib import Path

IMAGE = "jannesbrunner/health-coach"
TOML  = Path(__file__).parent / "pyproject.toml"


def bump_patch(version: str) -> str:
    major, minor, patch = version.split(".")
    return f"{major}.{minor}.{int(patch) + 1}"


def run(cmd: list[str]) -> None:
    print(f"\n$ {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        sys.exit(result.returncode)


def main() -> None:
    content = TOML.read_text(encoding="utf-8")
    match = re.search(r'^version = "(.+)"', content, re.MULTILINE)
    if not match:
        print("ERROR: version not found in pyproject.toml")
        sys.exit(1)

    current = match.group(1)
    new_version = bump_patch(current)

    TOML.write_text(
        content.replace(f'version = "{current}"', f'version = "{new_version}"'),
        encoding="utf-8",
    )
    print(f"Version: {current} → {new_version}")

    run(["docker", "build",
         "-t", f"{IMAGE}:{new_version}",
         "-t", f"{IMAGE}:latest",
         "."])

    run(["docker", "push", f"{IMAGE}:{new_version}"])
    run(["docker", "push", f"{IMAGE}:latest"])

    print(f"\nDone. Released {IMAGE}:{new_version}")


if __name__ == "__main__":
    main()
