"""Reject private artifacts and recognizable secrets in public source files."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_NAMES = {
    ".DS_Store",
    "credentials.json",
    "lillio-archive.toml",
    "storage-state.json",
}
FORBIDDEN_SUFFIXES = {
    ".db",
    ".har",
    ".key",
    ".log",
    ".pem",
    ".sqlite3",
}
PRIVATE_PARTS = {
    ".lillio-profile",
    "artifacts",
    "downloads",
    "exports",
}
CONTENT_PATTERNS = {
    "absolute macOS home path": re.compile("/" + "Users/" + r"[^/\s]+/"),
    "absolute Linux home path": re.compile("/" + "home/" + r"[^/\s]+/"),
    "absolute Windows home path": re.compile(r"[A-Za-z]:\\\\Users\\\\[^\\\\\s]+\\\\"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "GitHub personal token": re.compile(r"\b(?:ghp_|github_pat_)[A-Za-z0-9_]+"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
}


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [ROOT / value.decode() for value in result.stdout.split(b"\0") if value]


def staged_files() -> list[Path]:
    result = subprocess.run(
        [
            "git",
            "diff",
            "--cached",
            "--name-only",
            "--diff-filter=ACMR",
            "-z",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [ROOT / value.decode() for value in result.stdout.split(b"\0") if value]


def scan(paths: list[Path]) -> list[str]:
    errors = []
    for path in paths:
        relative = path.relative_to(ROOT)
        if path.name in FORBIDDEN_NAMES:
            errors.append(f"forbidden file: {relative}")
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            errors.append(f"forbidden file type: {relative}")
        if PRIVATE_PARTS.intersection(relative.parts):
            errors.append(f"private data directory: {relative}")
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for label, pattern in CONTENT_PATTERNS.items():
            if pattern.search(text):
                errors.append(f"{label}: {relative}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--staged",
        action="store_true",
        help="scan only files staged for commit",
    )
    args = parser.parse_args()
    errors = scan(staged_files() if args.staged else tracked_files())
    if errors:
        print("Public tree check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Public tree check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
