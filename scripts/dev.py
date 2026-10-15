"""Cross-platform task runner (the Makefile just calls this, so Windows works without make).

    python scripts/dev.py setup    # create .venv and install locked dependencies
    python scripts/dev.py demo     # run the service on http://127.0.0.1:8000 (replay mode if no key)
    python scripts/dev.py test     # offline test suite
    python scripts/dev.py check    # ruff + tests + secret scan
    python scripts/dev.py audit    # pip-audit against the lockfile
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv"
VPY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def run(*args: str | Path, env: dict | None = None) -> int:
    print("+", " ".join(str(a) for a in args), flush=True)
    return subprocess.call([str(a) for a in args], cwd=ROOT, env=env)


def setup() -> int:
    if sys.version_info < (3, 11):  # noqa: UP036 - runs under the system python, before the venv exists
        print(f"Python 3.11+ is required (this is {sys.version.split()[0]}).")
        return 1
    if not VPY.exists():
        print(f"creating virtualenv in {VENV}")
        venv.EnvBuilder(with_pip=True).create(VENV)
    rc = run(VPY, "-m", "pip", "install", "-q", "--disable-pip-version-check", "-r", "requirements-dev.lock")
    return rc or run(VPY, "-m", "pip", "install", "-q", "--disable-pip-version-check", "--no-deps", "-e", ".")


def ensure_setup() -> int:
    if not VPY.exists() or subprocess.call([str(VPY), "-c", "import blindspot, fastapi"], cwd=ROOT,
                                           stderr=subprocess.DEVNULL) != 0:
        return setup()
    return 0


def demo() -> int:
    if rc := ensure_setup():
        return rc
    env = dict(os.environ)
    port = env.get("PORT", "8000")
    has_key = bool(env.get("QLOO_API_KEY")) or _env_file_has_key()
    print("\nBlindspot demo", "(live: key found)" if has_key else
          "(replay mode: no QLOO_API_KEY, serving cache/snapshot only; nothing calls Qloo)")
    print(f"  health:  http://127.0.0.1:{port}/healthz")
    print(f"  status:  http://127.0.0.1:{port}/api/qloo/status")
    print(f"  search:  http://127.0.0.1:{port}/api/qloo/search?q=Radiohead\n")
    return run(VPY, "-m", "uvicorn", "blindspot.service.app:main_app", "--factory",
               "--host", env.get("HOST", "127.0.0.1"), "--port", port, "--no-proxy-headers", "--no-server-header", env=env)


def _env_file_has_key() -> bool:
    f = ROOT / ".env"
    if not f.is_file():
        return False
    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
        k, _, v = line.partition("=")
        if k.strip() == "QLOO_API_KEY" and v.strip():
            return True
    return False


def test() -> int:
    return ensure_setup() or run(VPY, "-m", "pytest")


def lint() -> int:
    return ensure_setup() or run(VPY, "-m", "ruff", "check", ".")


def scan() -> int:
    return run(VPY if VPY.exists() else sys.executable, "scripts/scan_secrets.py")


def audit() -> int:
    return ensure_setup() or run(VPY, "-m", "pip_audit", "-r", "requirements.lock", "--progress-spinner", "off")


def check() -> int:
    return lint() or test() or scan()


def clean() -> int:
    for p in (".pytest_cache", ".ruff_cache"):
        shutil.rmtree(ROOT / p, ignore_errors=True)
    return 0


TASKS = {f.__name__: f for f in (setup, demo, test, lint, scan, audit, check, clean)}

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in TASKS:
        print(__doc__)
        sys.exit(2)
    sys.exit(TASKS[sys.argv[1]]())
