"""Export a named query set from the cache into a minimal snapshot file (0 live calls).

    python scripts/export_snapshot.py [--file config/demo_queries.json] [--out data/snapshot/snapshot.json]
    python scripts/export_snapshot.py --synthetic   # dev only: snapshot built from the SYNTHETIC
                                                     # test fixtures, to exercise offline mode

The real snapshot holds Qloo-derived data, so it goes to data/ (gitignored) or the host's
secret storage, never into git. See docs/decisions.md (terms check).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _common import DATA, ROOT
from blindspot.config import Settings
from blindspot.qloo.cache import cache_key
from blindspot.qloo.client import QlooClient
from blindspot.qloo.snapshot import Snapshot, build_snapshot


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(ROOT / "config" / "demo_queries.json"))
    ap.add_argument("--out")
    ap.add_argument("--name", default="phase1-demo")
    ap.add_argument("--synthetic", action="store_true")
    a = ap.parse_args()
    queries = json.loads(Path(a.file).read_text(encoding="utf-8"))["queries"]
    # Replay settings: this script never needs the key or the network.
    client = QlooClient(Settings(qloo_mode="replay"), snapshot=Snapshot())
    entries, missing = [], []
    for q in queries:
        norm = client._validate(q["path"], q["params"])
        key = cache_key("GET", q["path"], norm)
        if a.synthetic:
            fx = "search_synthetic.json" if q["path"] == "/search" else "insights_synthetic.json"
            body = json.loads((ROOT / "tests" / "fixtures" / fx).read_text(encoding="utf-8"))
            entries.append(
                {
                    "key": key,
                    "label": q["label"],
                    "method": "GET",
                    "path": q["path"],
                    "params": norm,
                    "status": 200,
                    "fetched_at": 0.0,
                    "body": body,
                }
            )
            continue
        hit = client.cache.entries([key])
        if hit:
            entries.append({**hit[0], "label": q["label"]})
        else:
            missing.append(q["label"])
    snap = build_snapshot(entries, a.name + ("-SYNTHETIC" if a.synthetic else ""))
    if a.synthetic:
        snap["notice"] = "SYNTHETIC: built from invented test fixtures, not Qloo data. Dev/testing only."
    out = Path(a.out) if a.out else DATA / "snapshot" / ("synthetic.json" if a.synthetic else "snapshot.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    size = out.stat().st_size
    print(f"wrote {out} with {len(entries)} entries ({size / 1024:.1f} KiB)")
    if missing:
        print(f"not in cache (run warm_cache.py first): {', '.join(missing)}")
    client.db.close()
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
