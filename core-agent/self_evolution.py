from __future__ import annotations

import argparse
import difflib
import importlib
import json
import os
import py_compile
import shutil
import sys
import time
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from master_agent import NexusLLMRouter


@dataclass
class PatchEvent:
    timestamp: float
    status: str
    file_path: str
    backup_path: str
    message: str
    diff: str


class SelfEvolutionDaemon:
    """Guarded self-repair for NexusOS-Ultra.

    This daemon is intentionally constrained. It can patch files only inside the project source roots and only after
    producing compilable Python for Python files. It records every patch and keeps backups.
    """

    PYTHON_SUFFIXES = {".py"}
    TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".css", ".md", ".yml", ".yaml", ".toml", ".sh"}

    def __init__(self, router: Optional[NexusLLMRouter] = None) -> None:
        self.router = router or NexusLLMRouter()
        self.enabled = os.getenv("NEXUS_SELF_EVOLUTION", "0").strip() == "1"
        self.project_root = Path(os.getenv("NEXUS_PROJECT_ROOT", "/app")).resolve()
        self.patch_dir = Path(os.getenv("NEXUS_PATCH_DIR", "/app/runtime/patches")).resolve()
        self.log_dir = Path(os.getenv("NEXUS_LOG_DIR", "/app/runtime/logs")).resolve()
        self.max_patch_bytes = int(os.getenv("NEXUS_MAX_PATCH_BYTES", "200000"))
        self.allowed_roots = [
            (self.project_root / "core-agent").resolve(),
            (self.project_root / "browser-bridge").resolve(),
            (self.project_root / "gateway").resolve(),
            (self.project_root / "frontend" / "src").resolve(),
        ]
        self.patch_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def run_once(self, error_context: str = "") -> PatchEvent:
        if not self.enabled:
            event = PatchEvent(time.time(), "disabled", "", "", "Self-evolution is disabled. Set NEXUS_SELF_EVOLUTION=1.", "")
            self._record(event)
            return event

        context = self.collect_context(error_context)
        plan = self.propose_patch(context)
        event = self.apply_patch_plan(plan)
        self._record(event)
        return event

    def collect_context(self, error_context: str = "") -> str:
        chunks: List[str] = []
        if error_context.strip():
            chunks.append("EXTERNAL ERROR CONTEXT:\n" + error_context.strip())

        for log_file in sorted(self.log_dir.glob("*.log"))[-5:]:
            try:
                text = log_file.read_text(errors="replace")[-12_000:]
                chunks.append(f"LOG FILE: {log_file.name}\n{text}")
            except Exception:
                continue

        important_files = [
            self.project_root / "gateway" / "main.py",
            self.project_root / "core-agent" / "master_agent.py",
            self.project_root / "core-agent" / "filesystem_tools.py",
            self.project_root / "browser-bridge" / "skyvern_client.py",
        ]
        for file_path in important_files:
            if file_path.exists():
                try:
                    chunks.append(f"SOURCE FILE: {file_path}\n{file_path.read_text(errors='replace')[:30_000]}")
                except Exception:
                    continue

        if not chunks:
            chunks.append("No logs or source context were available.")
        return "\n\n---\n\n".join(chunks)[:120_000]

    def propose_patch(self, context: str) -> Dict[str, Any]:
        system = (
            "You are a safe repair engine for a local application. "
            "Return one JSON object only. Patch exactly one file. "
            "Allowed keys: file_path, replacement_text, message. "
            "Do not request secrets. Do not expand permissions. Do not disable safety guards."
        )
        prompt = (
            "Analyze the following NexusOS-Ultra logs/source context. "
            "If there is a clear code defect, return a complete replacement for the single safest file to patch. "
            "If no patch is justified, return file_path as an empty string and explain in message.\n\n"
            f"{context}"
        )
        parsed, _ = self.router.ask_json(
            prompt,
            task_type="repair",
            system=system,
            schema_hint={"file_path": "relative/path.py", "replacement_text": "complete file text", "message": "why this patch is safe"},
        )
        return parsed

    def apply_patch_plan(self, plan: Dict[str, Any]) -> PatchEvent:
        file_path = str(plan.get("file_path", "")).strip()
        replacement = str(plan.get("replacement_text", ""))
        message = str(plan.get("message", "")).strip() or "No message supplied."

        if not file_path:
            return PatchEvent(time.time(), "no_patch", "", "", message, "")

        target = (self.project_root / file_path.lstrip("/")).resolve()
        self._assert_allowed_target(target)

        if target.suffix not in self.TEXT_SUFFIXES:
            raise ValueError(f"Refusing to patch unsupported file type: {target.suffix}")

        encoded = replacement.encode("utf-8")
        if len(encoded) == 0:
            raise ValueError("Replacement text is empty.")
        if len(encoded) > self.max_patch_bytes:
            raise ValueError(f"Replacement exceeds NEXUS_MAX_PATCH_BYTES={self.max_patch_bytes}")

        old_text = target.read_text(errors="replace") if target.exists() else ""
        diff = "\n".join(
            difflib.unified_diff(
                old_text.splitlines(),
                replacement.splitlines(),
                fromfile=str(target),
                tofile=f"{target}.candidate",
                lineterm="",
            )
        )

        temp_path = target.with_suffix(target.suffix + ".candidate")
        temp_path.write_text(replacement, encoding="utf-8")

        if target.suffix in self.PYTHON_SUFFIXES:
            py_compile.compile(str(temp_path), doraise=True)

        backup = target.with_suffix(target.suffix + f".backup-{int(time.time())}")
        if target.exists():
            shutil.copy2(target, backup)
        target.write_text(replacement, encoding="utf-8")
        temp_path.unlink(missing_ok=True)

        if target.suffix in self.PYTHON_SUFFIXES:
            py_compile.compile(str(target), doraise=True)
            module_name = target.stem
            if module_name in sys.modules:
                importlib.reload(sys.modules[module_name])

        return PatchEvent(time.time(), "patched", str(target), str(backup), message, diff[-40_000:])

    def _assert_allowed_target(self, target: Path) -> None:
        for root in self.allowed_roots:
            try:
                target.relative_to(root)
                return
            except ValueError:
                continue
        raise ValueError(f"Target is outside allowed source roots: {target}")

    def _record(self, event: PatchEvent) -> None:
        path = self.patch_dir / f"patch-event-{int(event.timestamp)}.json"
        path.write_text(json.dumps(asdict(event), indent=2, ensure_ascii=False), encoding="utf-8")


def capture_exception_context(exc: BaseException) -> str:
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one guarded NexusOS-Ultra self-evolution pass.")
    parser.add_argument("--once", action="store_true", help="Run a single pass and exit.")
    parser.add_argument("--error-file", default="", help="Optional file containing error context.")
    args = parser.parse_args()

    context = ""
    if args.error_file:
        context = Path(args.error_file).read_text(errors="replace")
    daemon = SelfEvolutionDaemon()
    event = daemon.run_once(context)
    print(json.dumps(asdict(event), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
