#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
import py_compile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    ".env.example",
    "docker-compose.yml",
    "README.md",
    "core-agent/__init__.py",
    "core-agent/master_agent.py",
    "core-agent/filesystem_tools.py",
    "core-agent/self_evolution.py",
    "browser-bridge/__init__.py",
    "browser-bridge/skyvern_client.py",
    "gateway/Dockerfile",
    "gateway/main.py",
    "gateway/requirements.txt",
    "frontend/Dockerfile",
    "frontend/package.json",
    "frontend/postcss.config.js",
    "frontend/tailwind.config.js",
    "frontend/next.config.mjs",
    "frontend/tsconfig.json",
    "frontend/src/app/layout.tsx",
    "frontend/src/app/globals.css",
    "frontend/src/app/page.tsx",
    "frontend/src/components/ui/card.tsx",
    "frontend/src/components/ui/button.tsx",
    "frontend/src/components/ui/badge.tsx",
    "frontend/src/lib/utils.ts",
]

FORBIDDEN_MARKERS = [
    "TODO",
    "PLACEHOLDER",
    "[...]",
    "your_key_here",
    "INSERT_",
]


def fail(message: str) -> None:
    print(f"VALIDATION FAILED: {message}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    missing = []
    empty = []
    forbidden = []

    for rel in REQUIRED_FILES:
        path = ROOT / rel
        if not path.exists():
            missing.append(rel)
            continue
        data = path.read_text(errors="replace")
        if not data.strip():
            empty.append(rel)
        for marker in FORBIDDEN_MARKERS:
            if marker in data:
                forbidden.append(f"{rel}: {marker}")

    if missing:
        fail("missing files: " + ", ".join(missing))
    if empty:
        fail("empty files: " + ", ".join(empty))
    if forbidden:
        fail("forbidden unfinished markers: " + ", ".join(forbidden))

    for path in [ROOT / "frontend" / "package.json", ROOT / "frontend" / "tsconfig.json"]:
        try:
            json.loads(path.read_text())
        except Exception as exc:
            fail(f"invalid JSON in {path.relative_to(ROOT)}: {exc}")

    for rel in [
        "core-agent/master_agent.py",
        "core-agent/filesystem_tools.py",
        "core-agent/self_evolution.py",
        "browser-bridge/skyvern_client.py",
        "gateway/main.py",
        "scripts/validate_stack.py",
    ]:
        path = ROOT / rel
        try:
            py_compile.compile(str(path), doraise=True)
            ast.parse(path.read_text())
        except Exception as exc:
            fail(f"python syntax failed for {rel}: {exc}")

    page = (ROOT / "frontend" / "src" / "app" / "page.tsx").read_text()
    required_ui_terms = [
        "macOS Filesystem Mesh",
        "Visual Browser Stream",
        "Metric Log Window",
        "Self-Evolution Git Diff",
        "/api/files/tree",
        "/api/browser/task",
        "/api/evolution/run",
    ]
    absent = [term for term in required_ui_terms if term not in page]
    if absent:
        fail("frontend page is missing required UI terms: " + ", ".join(absent))

    compose = (ROOT / "docker-compose.yml").read_text()
    for mount in [
        "/Users/${USER}:/workspace/local_mac_system",
        "/Users/${USER}/Library/Mobile Documents/com~apple~CloudDocs:/workspace/icloud_drive",
    ]:
        if mount not in compose:
            fail(f"docker-compose missing mount: {mount}")

    print("NexusOS-Ultra validation passed.")


if __name__ == "__main__":
    main()
