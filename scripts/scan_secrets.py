"""Secret scanner. Never prints secret values, only where they were found.

    python scripts/scan_secrets.py                 # working tree + git history
    python scripts/scan_secrets.py --docker IMAGE  # also the image's history, config and layers

Looks for (1) the literal values of QLOO_API_KEY / IP_HASH_SALT from .env or the environment,
and (2) common key patterns. Exit code 1 if anything is found.
"""

from __future__ import annotations

import argparse
import io
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRET_VARS = ("QLOO_API_KEY", "IP_HASH_SALT")
PATTERNS = {
    "generic-api-key-assignment": re.compile(
        rb"(?i)(api[_-]?key|secret|token|passwd|password)[\"']?\s*[:=]\s*[\"']([A-Za-z0-9_\-]{24,})[\"']"),
    "x-api-key-header": re.compile(rb"(?i)x-api-key[\"']?\s*:\s*[\"']?[A-Za-z0-9_\-]{20,}"),
    "aws-access-key": re.compile(rb"AKIA[0-9A-Z]{16}"),
    "private-key": re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "github-token": re.compile(rb"gh[pousr]_[A-Za-z0-9]{36,}"),
}
IGNORE_MARK = b"scan-secrets: ignore"
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "data", "__pycache__", ".ruff_cache", ".pytest_cache"}


def secret_values() -> dict[str, bytes]:
    vals: dict[str, str] = {}
    env_file = ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip() in SECRET_VARS:
                vals[k.strip()] = v.strip().strip("'\"")
    for k in SECRET_VARS:
        if os.environ.get(k):
            vals.setdefault(k, os.environ[k].strip())
    return {k: v.encode() for k, v in vals.items() if len(v) >= 6}


def scan_bytes(data: bytes, where: str, secrets: dict[str, bytes], findings: list[str]) -> None:
    for name, val in secrets.items():
        if val in data:
            findings.append(f"{where}: contains the value of {name}")
    for line_no, line in enumerate(data.splitlines(), 1):
        if IGNORE_MARK in line:
            continue
        for pname, pat in PATTERNS.items():
            if pat.search(line):
                findings.append(f"{where}:{line_no}: matches pattern {pname}")


def tracked_and_untracked_files() -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=ROOT,
                             capture_output=True, check=True).stdout
        return [ROOT / p.decode() for p in out.split(b"\0") if p]
    except (OSError, subprocess.CalledProcessError):
        return [p for p in ROOT.rglob("*") if p.is_file() and not SKIP_DIRS & set(p.parts)]


def scan_tree(secrets, findings) -> int:
    files = tracked_and_untracked_files()
    for p in files:
        if p.name == ".env" or not p.is_file():
            continue
        try:
            scan_bytes(p.read_bytes(), str(p.relative_to(ROOT)), secrets, findings)
        except OSError:
            pass
    return len(files)


def scan_history(secrets, findings) -> int:
    try:
        revs = subprocess.run(["git", "rev-list", "--all"], cwd=ROOT, capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return 0
    commits = revs.stdout.split()
    if not commits:
        return 0
    log = subprocess.run(["git", "log", "--all", "-p", "--no-color", "--format=commit %H%n%B"],
                         cwd=ROOT, capture_output=True, check=True).stdout
    # Only added lines matter for patterns; secret values are checked across everything.
    for name, val in secrets.items():
        if val in log:
            findings.append(f"git history: contains the value of {name}")
    added = b"\n".join(line[1:] for line in log.splitlines() if line.startswith(b"+")
                       and not line.startswith(b"+++"))
    scan_bytes(added, "git history (added lines)", {}, findings)
    return len(commits)


def scan_docker(image: str, secrets, findings) -> None:
    for args, label in ((["docker", "history", "--no-trunc", image], "docker history"),
                        (["docker", "inspect", image], "docker inspect")):
        out = subprocess.run(args, capture_output=True, check=True).stdout
        scan_bytes(out, label, secrets, findings)
    with tempfile.TemporaryDirectory() as td:
        tar_path = Path(td) / "image.tar"
        subprocess.run(["docker", "save", "-o", str(tar_path), image], check=True)
        n = 0
        with tarfile.open(tar_path) as outer:
            for m in outer.getmembers():
                if not m.isfile():
                    continue
                f = outer.extractfile(m)
                if f is None:
                    continue
                n += 1
                scan_layer(f.read(), m.name, secrets, findings)
        print(f"scanned docker image {image}: {n} blobs")


def scan_layer(blob: bytes, label: str, secrets, findings) -> None:
    """Layers are tars themselves: scan each file inside for secret values and .env files.
    (Pattern-scanning third-party site-packages would only produce noise.)"""
    try:
        with tarfile.open(fileobj=io.BytesIO(blob)) as layer:
            for lm in layer.getmembers():
                if lm.name == ".env" or lm.name.endswith("/.env"):
                    findings.append(f"docker {label}: contains a .env file ({lm.name})")
                if not lm.isfile() or lm.size > 20_000_000:
                    continue
                lf = layer.extractfile(lm)
                data = lf.read() if lf is not None else b""
                for name, val in secrets.items():
                    if val in data:
                        findings.append(f"docker {label}:{lm.name}: value of {name}")
    except tarfile.TarError:
        for name, val in secrets.items():
            if val in blob:
                findings.append(f"docker blob {label}: value of {name}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docker", metavar="IMAGE")
    ap.add_argument("--no-history", action="store_true")
    a = ap.parse_args()
    secrets = secret_values()
    findings: list[str] = []
    print(f"secret values loaded for: {', '.join(sorted(secrets)) or 'none (no .env / env vars)'}")
    n = scan_tree(secrets, findings)
    print(f"scanned working tree: {n} files")
    if not a.no_history:
        print(f"scanned git history: {scan_history(secrets, findings)} commits")
    if a.docker:
        scan_docker(a.docker, secrets, findings)
    if findings:
        print("\nFOUND POSSIBLE SECRETS:")
        for f in sorted(set(findings)):
            print("  " + f)
        return 1
    print("OK: no secrets found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
