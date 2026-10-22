"""`blindspot <group> ...`: the project's command line. Groups: scenarios, groundtruth, target, param.

Also runs as `python -m blindspot ...`, which works without reinstalling the package.
"""

from __future__ import annotations

import sys
from pathlib import Path

GROUPS = {
    "scenarios": "blindspot.scenarios.cli",
    "groundtruth": "blindspot.groundtruth.cli",
    "target": "blindspot.targets.cli",
    "param": "",
}


def _param(args: list[str]) -> int:
    """`blindspot param promote ...`: a thin call into scripts/promote_param.py (repo checkouts only)."""
    script = Path(__file__).resolve().parents[2] / "scripts" / "promote_param.py"
    if not args or args[0] != "promote":
        print("usage: blindspot param promote <filter.type> <param> <value> [options]", file=sys.stderr)
        return 2
    if not script.is_file():
        print(f"{script} not found: `param promote` needs a repo checkout", file=sys.stderr)
        return 2
    sys.path.insert(0, str(script.parent))
    from promote_param import main as promote

    return promote(args[1:])


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] not in GROUPS:
        print(f"usage: blindspot {{{','.join(GROUPS)}}} ...", file=sys.stderr)
        return 2
    if args[0] == "param":
        return _param(args[1:])
    from importlib import import_module

    return import_module(GROUPS[args[0]]).main(args[1:])


if __name__ == "__main__":
    sys.exit(main())
