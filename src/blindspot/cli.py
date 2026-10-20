"""`blindspot <group> ...`: the project's command line. Groups: scenarios (#16), groundtruth (#18).

Also runs as `python -m blindspot ...`, which works without reinstalling the package.
"""

from __future__ import annotations

import sys

GROUPS = {"scenarios": "blindspot.scenarios.cli", "groundtruth": "blindspot.groundtruth.cli"}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] not in GROUPS:
        print(f"usage: blindspot {{{','.join(GROUPS)}}} ...", file=sys.stderr)
        return 2
    from importlib import import_module

    return import_module(GROUPS[args[0]]).main(args[1:])


if __name__ == "__main__":
    sys.exit(main())
