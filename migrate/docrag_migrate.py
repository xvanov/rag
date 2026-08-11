#!/usr/bin/env python3
"""docrag_migrate.py -- portable export/import of a docrag-style deployment.

A docrag deployment is "compute-in-cloud, data-on-disk": the embeddings + LLM
live in Azure OpenAI, but the *index* (SQLite + sqlite-vec + FTS5 ``.db`` files)
and the *source corpora* live locally. Moving an instance to another machine
(Linux or Windows) therefore means moving those data artifacts + secrets, then
rebuilding the regenerable runtime (venv / container) on the target.

This script is **config-driven** so it works for any docrag-style repo (the
building-codes one here, the separate YouTube one, future ones): point it at a
manifest describing what to bundle. Stdlib-only -- runs on any Python 3.9+ with
no install.

  export:  checkpoint every index DB (fold WAL into the .db so it copies clean),
           tar.gz the index + corpora + secrets, write a manifest with sizes,
           sha256, source OS, and git rev.

  import:  extract the bundle into a target repo root, rewrite .mcp.json's python
           path for the target OS, optionally bootstrap a venv, run a self-test.

Usage:
  python migrate/docrag_migrate.py export \
      --manifest migrate/manifests/docrag.json \
      --out /tmp/docrag-bundle.tar.gz

  python migrate/docrag_migrate.py import \
      --bundle /tmp/docrag-bundle.tar.gz \
      --repo-root /opt/docrag \
      [--python /opt/docrag/.venv/bin/python] [--self-test]

Run `export --help` / `import --help` for all flags.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
from pathlib import Path

# Files that must never travel in a bundle: WAL/shm are checkpointed away;
# pidfiles are host-specific; caches regenerate.
_EXCLUDE_GLOBS = ["*.db-wal", "*.db-shm", "*.pid", "__pycache__", "*.pyc"]

_MANIFEST_NAME = "BUNDLE_MANIFEST.json"


# ---------------------------------------------------------------- helpers

def _eprint(*a: object) -> None:
    print(*a, file=sys.stderr, flush=True)


def _excluded(rel: str) -> bool:
    base = os.path.basename(rel)
    return any(fnmatch.fnmatch(base, g) or fnmatch.fnmatch(rel, g) for g in _EXCLUDE_GLOBS)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def _git_rev(repo_root: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def _load_manifest(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        m = json.load(f)
    m.setdefault("name", "docrag")
    m.setdefault("index_dir", ".index")
    m.setdefault("data_dirs", ["corpora"])
    m.setdefault("include_files", [".env", "app.settings.json", ".mcp.json"])
    m.setdefault("extra_dirs", [])  # e.g. .index/traces handled by index_dir sweep
    return m


def _resolve_repo_root(manifest: dict, override: str | None) -> Path:
    if override:
        return Path(override).expanduser().resolve()
    rr = manifest.get("repo_root")
    if rr:
        return Path(rr).expanduser().resolve()
    # default: parent of migrate/ (i.e. the repo this script ships in)
    return Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- checkpoint

def _checkpoint_dbs(index_dir: Path) -> list[str]:
    """Fold each WAL into its .db (TRUNCATE) so the .db copies as a consistent
    single file -- no need to drag -wal/-shm and risk a torn copy. Pure stdlib
    sqlite3; does not need the sqlite-vec extension just to checkpoint."""
    done = []
    if not index_dir.is_dir():
        return done
    for db in sorted(index_dir.glob("*.db")):
        try:
            conn = sqlite3.connect(str(db))
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.close()
            done.append(db.name)
        except sqlite3.Error as e:
            _eprint(f"  ! checkpoint failed for {db.name}: {e} (copying as-is)")
    return done


def _server_running(index_dir: Path) -> str | None:
    pid = index_dir / "docrag_server.pid"
    if pid.is_file():
        try:
            return pid.read_text(encoding="utf-8").splitlines()[0].strip()
        except Exception:  # noqa: BLE001
            return "?"
    return None


# ---------------------------------------------------------------- export

def _iter_bundle_members(repo_root: Path, manifest: dict):
    """Yield (abs_path, arcname) pairs to add to the tar, arcname relative to
    the repo root so the bundle restores into any target repo root."""
    seen = set()

    yield_list: list[tuple[Path, str]] = []

    def add_file(p: Path) -> None:
        rel = os.path.relpath(p, repo_root)
        if _excluded(rel) or rel in seen or not p.is_file():
            return
        seen.add(rel)
        yield_list.append((p, rel))

    # index dir (db files + traces/feedback subdirs), minus excluded globs
    index_dir = repo_root / manifest["index_dir"]
    if index_dir.is_dir():
        for p in index_dir.rglob("*"):
            if p.is_file():
                add_file(p)

    # corpora / data dirs
    for d in manifest["data_dirs"]:
        dd = repo_root / d
        if dd.is_dir():
            for p in dd.rglob("*"):
                if p.is_file():
                    add_file(p)
        elif dd.exists():
            add_file(dd)

    for d in manifest.get("extra_dirs", []):
        dd = repo_root / d
        if dd.is_dir():
            for p in dd.rglob("*"):
                if p.is_file():
                    add_file(p)

    # single include files (.env, .mcp.json, app.settings.json, ...)
    for f in manifest["include_files"]:
        add_file(repo_root / f)

    return yield_list


def cmd_export(args: argparse.Namespace) -> int:
    manifest = _load_manifest(Path(args.manifest))
    repo_root = _resolve_repo_root(manifest, args.repo_root)
    index_dir = repo_root / manifest["index_dir"]

    _eprint(f"== export {manifest['name']} from {repo_root}")

    running = _server_running(index_dir)
    if running and not args.force:
        _eprint(f"  ! server appears to be running (pid {running}).")
        _eprint("    Stop it first (server/server.py --stop) or pass --force to "
                "checkpoint a live DB anyway.")
        return 2

    if not args.no_checkpoint:
        ck = _checkpoint_dbs(index_dir)
        _eprint(f"  checkpointed: {', '.join(ck) if ck else '(none)'}")

    members = _iter_bundle_members(repo_root, manifest)
    if not members:
        _eprint("  ! nothing to bundle -- check manifest paths.")
        return 2

    out = Path(args.out).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    files_meta = []
    total = 0
    _eprint(f"  packing {len(members)} files ...")
    with tarfile.open(out, "w:gz") as tar:
        for abs_path, arc in members:
            size = abs_path.stat().st_size
            total += size
            files_meta.append({
                "path": arc.replace("\\", "/"),
                "size": size,
                "sha256": _sha256(abs_path) if args.checksums else None,
            })
            tar.add(abs_path, arcname=arc)

        bundle_manifest = {
            "tool": "docrag_migrate",
            "format": 1,
            "name": manifest["name"],
            "created_epoch": int(time.time()),
            "source": {
                "os": platform.system(),
                "platform": platform.platform(),
                "python": platform.python_version(),
                "hostname": platform.node(),
                "git_rev": _git_rev(repo_root),
            },
            "layout": {
                "index_dir": manifest["index_dir"],
                "data_dirs": manifest["data_dirs"],
                "include_files": manifest["include_files"],
            },
            "schema_note": "SQLite/sqlite-vec index; SCHEMA_VERSION lives in docrag/db.py. "
                           "Re-index if the target code expects a newer schema.",
            "file_count": len(files_meta),
            "total_bytes": total,
            "files": files_meta,
        }
        data = json.dumps(bundle_manifest, indent=2).encode("utf-8")
        info = tarfile.TarInfo(_MANIFEST_NAME)
        info.size = len(data)
        info.mtime = int(time.time())
        import io
        tar.addfile(info, io.BytesIO(data))

    gz = out.stat().st_size
    _eprint(f"  wrote {out}")
    _eprint(f"  {len(files_meta)} files, {total/1e9:.2f} GB raw -> {gz/1e9:.2f} GB gzip")
    if not args.checksums:
        _eprint("  (sha256 skipped; pass --checksums to record per-file hashes)")
    return 0


# ---------------------------------------------------------------- import

def _safe_extract(tar: tarfile.TarFile, dest: Path) -> None:
    """Extract with path-traversal guard (no member escapes dest)."""
    dest = dest.resolve()
    for m in tar.getmembers():
        target = (dest / m.name).resolve()
        if not str(target).startswith(str(dest)):
            raise RuntimeError(f"unsafe path in bundle: {m.name}")
    tar.extractall(dest)


def _rewrite_mcp_json(repo_root: Path, python_path: str) -> bool:
    mcp = repo_root / ".mcp.json"
    if not mcp.is_file():
        return False
    try:
        data = json.loads(mcp.read_text(encoding="utf-8"))
        changed = False
        for srv in data.get("mcpServers", {}).values():
            if "command" in srv:
                srv["command"] = python_path
                changed = True
        if changed:
            mcp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return changed
    except Exception as e:  # noqa: BLE001
        _eprint(f"  ! could not rewrite .mcp.json: {e}")
        return False


def cmd_import(args: argparse.Namespace) -> int:
    bundle = Path(args.bundle).expanduser().resolve()
    repo_root = Path(args.repo_root).expanduser().resolve()
    repo_root.mkdir(parents=True, exist_ok=True)

    _eprint(f"== import {bundle} -> {repo_root}")
    with tarfile.open(bundle, "r:gz") as tar:
        try:
            bm = json.loads(tar.extractfile(_MANIFEST_NAME).read().decode("utf-8"))
            _eprint(f"  bundle: {bm['name']}  from {bm['source']['os']} "
                    f"(git {bm['source']['git_rev']}, {bm['file_count']} files)")
        except Exception:  # noqa: BLE001
            _eprint("  (no BUNDLE_MANIFEST.json -- extracting blind)")
        _safe_extract(tar, repo_root)
    _eprint("  extracted.")

    # Rewrite the MCP python path for the target machine.
    py = args.python
    if not py:
        # Sensible default for the target OS.
        if platform.system() == "Windows":
            py = str(repo_root / ".venv" / "Scripts" / "python.exe")
        else:
            py = str(repo_root / ".venv" / "bin" / "python")
    if _rewrite_mcp_json(repo_root, py):
        _eprint(f"  .mcp.json command -> {py}")

    if args.bootstrap_venv:
        rc = _bootstrap_venv(repo_root, args.requirements)
        if rc != 0:
            return rc

    if args.self_test:
        rc = _self_test(repo_root, py)
        if rc != 0:
            return rc

    _eprint("  done.")
    return 0


def _bootstrap_venv(repo_root: Path, requirements: str) -> int:
    venv = repo_root / ".venv"
    req = repo_root / requirements
    if not req.is_file():
        _eprint(f"  ! requirements file not found: {req}")
        return 2
    _eprint(f"  creating venv at {venv} ...")
    rc = subprocess.run([sys.executable, "-m", "venv", str(venv)]).returncode
    if rc != 0:
        return rc
    py = venv / ("Scripts/python.exe" if platform.system() == "Windows" else "bin/python")
    _eprint(f"  pip install -r {req} (this can take a while) ...")
    subprocess.run([str(py), "-m", "pip", "install", "--upgrade", "pip"])
    return subprocess.run([str(py), "-m", "pip", "install", "-r", str(req)]).returncode


def _self_test(repo_root: Path, py: str) -> int:
    if not Path(py).exists():
        _eprint(f"  ! python not found for self-test: {py} (skipping)")
        return 0
    _eprint("  self-test: python -m docrag.db --self-test")
    return subprocess.run([py, "-m", "docrag.db", "--self-test"], cwd=str(repo_root)).returncode


# ---------------------------------------------------------------- cli

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Portable export/import for a docrag deployment.")
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("export", help="bundle index + corpora + secrets into a tar.gz")
    e.add_argument("--manifest", required=True, help="path to a bundle manifest JSON")
    e.add_argument("--out", required=True, help="output .tar.gz path")
    e.add_argument("--repo-root", help="override repo root (else manifest/default)")
    e.add_argument("--checksums", action="store_true", help="record per-file sha256 (slower)")
    e.add_argument("--no-checkpoint", action="store_true", help="skip WAL checkpoint")
    e.add_argument("--force", action="store_true", help="export even if server seems running")
    e.set_defaults(func=cmd_export)

    i = sub.add_parser("import", help="restore a bundle into a target repo root")
    i.add_argument("--bundle", required=True, help="path to a .tar.gz produced by export")
    i.add_argument("--repo-root", required=True, help="target repo root to extract into")
    i.add_argument("--python", help="python path to write into .mcp.json (else OS default)")
    i.add_argument("--bootstrap-venv", action="store_true", help="create .venv + pip install")
    i.add_argument("--requirements", default="requirements.txt", help="requirements file for --bootstrap-venv")
    i.add_argument("--self-test", action="store_true", help="run docrag.db --self-test after")
    i.set_defaults(func=cmd_import)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
