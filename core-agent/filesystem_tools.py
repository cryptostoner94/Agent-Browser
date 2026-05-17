from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal, Optional


RootName = Literal["local", "icloud", "exports"]


class SafePathError(ValueError):
    pass


@dataclass
class FileNode:
    name: str
    path: str
    kind: Literal["file", "directory", "symlink", "other"]
    size: int
    modified_at: float
    readonly: bool
    children: Optional[List["FileNode"]] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        if self.children is not None:
            payload["children"] = [child.to_dict() for child in self.children]
        return payload


class MacFilesystemMesh:
    """Guarded file access for local Mac and iCloud bind mounts.

    The mesh never follows operations outside configured roots. Writes avoid Apple sync internals and
    system metadata paths that can destabilize iCloud or Spotlight background workers.
    """

    BLOCKED_PATH_PARTS = {
        ".Trash",
        ".Trashes",
        ".DocumentRevisions-V100",
        ".TemporaryItems",
        ".Spotlight-V100",
        ".fseventsd",
        ".vol",
        ".MobileBackups",
        ".apdisk",
        "__MACOSX",
        "node_modules",
        ".git",
        ".next",
        ".venv",
        "venv",
    }

    BLOCKED_SUFFIXES_FOR_WRITE = {
        ".icloud",
        ".nosync",
        ".download",
        ".tmp",
        ".swp",
        ".part",
    }

    TEXT_EXTENSIONS = {
        ".txt",
        ".md",
        ".json",
        ".yaml",
        ".yml",
        ".csv",
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".css",
        ".html",
        ".xml",
        ".toml",
        ".ini",
        ".log",
        ".sh",
        ".sql",
    }

    def __init__(self) -> None:
        self.local_root = Path(os.getenv("LOCAL_MAC_MOUNT", "/workspace/local_mac_system")).resolve()
        self.icloud_root = Path(os.getenv("ICLOUD_DRIVE_MOUNT", "/workspace/icloud_drive")).resolve()
        self.exports_root = Path(os.getenv("NEXUS_EXPORT_DIR", "/app/runtime/exports")).resolve()
        self.max_read_bytes = int(os.getenv("NEXUS_MAX_FILE_READ_BYTES", "2000000"))
        self.roots: Dict[RootName, Path] = {
            "local": self.local_root,
            "icloud": self.icloud_root,
            "exports": self.exports_root,
        }
        self.exports_root.mkdir(parents=True, exist_ok=True)

    def root_status(self) -> Dict[str, Any]:
        status = {}
        for name, root in self.roots.items():
            status[name] = {
                "path": str(root),
                "exists": root.exists(),
                "is_dir": root.is_dir(),
                "readable": os.access(root, os.R_OK) if root.exists() else False,
                "writable": os.access(root, os.W_OK) if root.exists() else False,
            }
        return status

    def resolve(self, root: RootName, relative_path: str = "") -> Path:
        if root not in self.roots:
            raise SafePathError(f"Unknown root: {root}")
        base = self.roots[root]
        candidate = (base / relative_path.lstrip("/")).resolve()
        self._assert_inside(base, candidate)
        self._assert_no_blocked_parts(candidate)
        return candidate

    def list_tree(self, root: RootName, relative_path: str = "", depth: int = 2, max_entries: int = 300) -> FileNode:
        target = self.resolve(root, relative_path)
        if not target.exists():
            raise FileNotFoundError(str(target))
        return self._node_for(target, self.roots[root], depth=max(0, min(depth, 5)), max_entries=max_entries)

    def read_file(self, root: RootName, relative_path: str, encoding: str = "utf-8") -> Dict[str, Any]:
        target = self.resolve(root, relative_path)
        if not target.is_file():
            raise SafePathError(f"Not a file: {relative_path}")
        if target.stat().st_size > self.max_read_bytes:
            raise SafePathError(f"File exceeds max read size of {self.max_read_bytes} bytes.")
        suffix = target.suffix.lower()
        data = target.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if suffix in self.TEXT_EXTENSIONS or self._looks_text(data):
            text = data.decode(encoding, errors="replace")
            return {
                "root": root,
                "path": self._relative(target, self.roots[root]),
                "encoding": encoding,
                "sha256": digest,
                "bytes": len(data),
                "text": text,
                "binary": False,
            }
        return {
            "root": root,
            "path": self._relative(target, self.roots[root]),
            "sha256": digest,
            "bytes": len(data),
            "text": "",
            "binary": True,
        }

    def write_file(
        self,
        root: RootName,
        relative_path: str,
        content: str,
        encoding: str = "utf-8",
        overwrite: bool = True,
    ) -> Dict[str, Any]:
        target = self.resolve(root, relative_path)
        self._assert_write_safe(target)
        if target.exists() and not overwrite:
            raise FileExistsError(str(target))
        target.parent.mkdir(parents=True, exist_ok=True)
        backup_path = None
        if target.exists() and target.is_file():
            backup_path = target.with_suffix(target.suffix + f".bak-{int(time.time())}")
            shutil.copy2(target, backup_path)
        encoded = content.encode(encoding)
        target.write_bytes(encoded)
        return {
            "root": root,
            "path": self._relative(target, self.roots[root]),
            "bytes": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "backup": str(backup_path) if backup_path else "",
        }

    def copy_file(self, source_root: RootName, source_path: str, target_root: RootName, target_path: str) -> Dict[str, Any]:
        src = self.resolve(source_root, source_path)
        dst = self.resolve(target_root, target_path)
        if not src.is_file():
            raise SafePathError(f"Source is not a file: {source_path}")
        self._assert_write_safe(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return {
            "source": self._relative(src, self.roots[source_root]),
            "target": self._relative(dst, self.roots[target_root]),
            "bytes": dst.stat().st_size,
        }

    def delete_file(self, root: RootName, relative_path: str) -> Dict[str, Any]:
        target = self.resolve(root, relative_path)
        self._assert_write_safe(target)
        if not target.is_file():
            raise SafePathError("Only file deletion is supported by the mesh API.")
        archive_dir = self.exports_root / "deleted"
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_name = f"{int(time.time())}-{hashlib.sha1(str(target).encode()).hexdigest()[:10]}-{target.name}"
        archive_path = archive_dir / archive_name
        shutil.move(str(target), str(archive_path))
        return {"deleted_path": self._relative(target, self.roots[root]), "archived_to": str(archive_path)}

    def write_json_export(self, name: str, payload: Any) -> Dict[str, Any]:
        safe_name = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in name).strip("._")
        if not safe_name:
            safe_name = f"export-{int(time.time())}.json"
        if not safe_name.endswith(".json"):
            safe_name += ".json"
        return self.write_file("exports", safe_name, json.dumps(payload, indent=2, ensure_ascii=False), overwrite=True)

    def _node_for(self, path: Path, base: Path, depth: int, max_entries: int) -> FileNode:
        try:
            st = path.lstat()
        except OSError:
            return FileNode(path.name, self._relative(path, base), "other", 0, 0.0, True, None)

        mode = st.st_mode
        if stat.S_ISDIR(mode):
            kind: Literal["file", "directory", "symlink", "other"] = "directory"
        elif stat.S_ISREG(mode):
            kind = "file"
        elif stat.S_ISLNK(mode):
            kind = "symlink"
        else:
            kind = "other"

        node = FileNode(
            name=path.name or str(base),
            path=self._relative(path, base),
            kind=kind,
            size=st.st_size,
            modified_at=st.st_mtime,
            readonly=not os.access(path, os.W_OK),
            children=None,
        )

        if kind == "directory" and depth > 0:
            children: List[FileNode] = []
            count = 0
            try:
                entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            except OSError:
                entries = []
            for entry in entries:
                if count >= max_entries:
                    break
                if self._is_blocked_entry(entry):
                    continue
                try:
                    self._assert_inside(base, entry.resolve())
                    children.append(self._node_for(entry, base, depth - 1, max_entries))
                    count += 1
                except Exception:
                    continue
            node.children = children
        return node

    def _assert_inside(self, base: Path, candidate: Path) -> None:
        try:
            candidate.relative_to(base)
        except ValueError as exc:
            raise SafePathError(f"Path escapes allowed root: {candidate}") from exc

    def _assert_no_blocked_parts(self, path: Path) -> None:
        parts = set(path.parts)
        found = sorted(parts.intersection(self.BLOCKED_PATH_PARTS))
        if found:
            raise SafePathError(f"Path contains blocked sync/system segment: {', '.join(found)}")

    def _assert_write_safe(self, path: Path) -> None:
        self._assert_no_blocked_parts(path)
        if any(path.name.endswith(suffix) for suffix in self.BLOCKED_SUFFIXES_FOR_WRITE):
            raise SafePathError(f"Refusing to write unsafe sync placeholder: {path.name}")
        if path.name.startswith(".") and path.suffix not in {".md", ".txt", ".json", ".log"}:
            raise SafePathError("Refusing to write hidden/system file through mesh.")
        if self.icloud_root in path.parents and "Mobile Documents" in str(path):
            if any(part.lower() in {"com~apple~clouddocs", "documents"} for part in path.parts):
                return

    def _is_blocked_entry(self, path: Path) -> bool:
        if path.name in self.BLOCKED_PATH_PARTS:
            return True
        if path.name.startswith(".") and path.name not in {".env.example"}:
            return True
        if any(path.name.endswith(suffix) for suffix in self.BLOCKED_SUFFIXES_FOR_WRITE):
            return True
        return False

    def _relative(self, path: Path, base: Path) -> str:
        try:
            rel = path.resolve().relative_to(base.resolve())
            text = str(rel)
            return "" if text == "." else text
        except Exception:
            return str(path)

    def _looks_text(self, data: bytes) -> bool:
        if not data:
            return True
        sample = data[:4096]
        if b"\x00" in sample:
            return False
        try:
            sample.decode("utf-8")
            return True
        except UnicodeDecodeError:
            return False


if __name__ == "__main__":
    mesh = MacFilesystemMesh()
    print(json.dumps(mesh.root_status(), indent=2))
