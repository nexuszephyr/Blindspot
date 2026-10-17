"""Create or update labels, milestones, issues and "blocked by" links from issues_spec.py.

Idempotent: existing issues are found by the `**Key:**` marker in their body (then by title), and
updated in place. Dependency links are made to match the spec exactly (missing ones added, extra
ones removed). The `critical-path` label is computed (zero slack to S2), not taken from the spec.

Usage: python scripts/planning/create_issues.py scripts/planning [--dry KEY ...]
"""

import importlib.util
import json
import re
import subprocess
import sys
import time

REPO = "repos/nexuszephyr/Blindspot"
HERE = sys.argv[1]
spec = importlib.util.spec_from_file_location("s", f"{HERE}/issues_spec.py")
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)
FINAL = "S2"


def api(method, path, payload=None, ok_fail=False):
    cmd = ["gh", "api", "-X", method, f"{REPO}{path}", "-H", "Accept: application/vnd.github+json"]
    if payload is not None:
        cmd += ["--input", "-"]
    r = subprocess.run(
        cmd, input=json.dumps(payload) if payload is not None else None, capture_output=True, text=True
    )
    if r.returncode != 0:
        if ok_fail:
            return None
        raise SystemExit(f"{method} {path} failed: {r.stdout[:400]} {r.stderr[:400]}")
    return json.loads(r.stdout) if r.stdout.strip() else {}


def paged(path):
    out, page = [], 1
    while True:
        sep = "&" if "?" in path else "?"
        batch = api("GET", f"{path}{sep}per_page=100&page={page}")
        out += batch
        if len(batch) < 100:
            return out
        page += 1


by = {i["key"]: i for i in s.ISSUES}
blocks = {k: [] for k in by}
for i in s.ISSUES:
    for d in i["deps"]:
        blocks[d].append(i["key"])

memo, hmemo = {}, {}


def depth(k):
    if k not in memo:
        memo[k] = 1 + max([depth(d) for d in by[k]["deps"]] or [0])
    return memo[k]


def height(k):
    if k not in hmemo:
        if k == FINAL:
            hmemo[k] = 1
        else:
            best = max([h for h in (height(b) for b in blocks[k]) if h] or [0])
            hmemo[k] = best + 1 if best else 0
    return hmemo[k]


ZERO_SLACK = {k for k in by if height(k) and depth(k) + height(k) - 1 == depth(FINAL)}


def labels_for(i):
    labs = [x for x in i["labels"] if x != "critical-path"]
    return [*labs, "critical-path"] if i["key"] in ZERO_SLACK else labs


def common_checks(i):
    labs = set(i["labels"])
    out = []
    if "needs-user" in labs:
        out.append(
            "Comment on this issue with the evidence (URLs, screenshots, counts; never secrets or Qloo data), then close it"
        )
    else:
        out += [
            "`make test` (or `python scripts/dev.py test`) passes offline with `.env` moved aside",
            "`ruff check .` and `ruff format --check .` clean",
            "`python scripts/scan_secrets.py` clean; no real Qloo data added to git (D5)",
            "CI green on Ubuntu and Windows, including the fresh-clone no-key job",
        ]
    has_budget = any("cli budget" in c for c in i["checks"])
    if "qloo-api" in labs and "needs-user" not in labs and not has_budget:
        out.append(
            "Run `python -m blindspot.qloo.cli budget` before and after; put live calls by purpose (from `live_attempts`) in the PR"
        )
    if "llm" in labs and "needs-user" not in labs:
        out.append(
            "No model output reaches ground truth or a score; prompts never contain answer-key entities; token usage reported in the PR"
        )
    if "security" in labs and "needs-user" not in labs:
        out.append(
            "Mutation spot-check: removing each protection added here makes a test fail (record in the PR)"
        )
    if "ui" in labs:
        out.append(
            "PR includes desktop and 360 px screenshots, light and dark; keyboard-only pass; data label visible"
        )
    if "docs" in labs:
        out.append(
            "All links and anchors resolve; committed docs hold aggregates only, no Qloo names, ids or scores"
        )
    if "judge-experience" in labs and "needs-user" not in labs:
        out.append("Tried as a judge would: fresh browser, no key, hosted or `make demo`")
    return out


def bullets(items, box=False):
    pre = "- [ ] " if box else "- "
    return [pre + x for x in items]


KEY_REF = re.compile(r"#(U\d+|P\d-\d+|S\d|B\d)\b")


def link_keys(text, num):
    return KEY_REF.sub(lambda m: f"#{num[m.group(1)]}" if m.group(1) in num else m.group(0), text)


def body_for(i, num):
    return link_keys(_body_for(i, num), num)


def _body_for(i, num):
    labs = labels_for(i)
    prio = next(x for x in labs if x in ("P0", "P1", "P2"))
    size = next(x for x in labs if x.startswith("size:"))[5:]
    head = f"**Key:** `{i['key']}` · **Milestone:** {i['ms']} · **Priority:** {prio} · **Size:** {size}"
    if i["key"] in ZERO_SLACK:
        head += " · ⭐ **zero slack** (on the critical path)"
    L = [head, "", "## Why", i["why"]]
    if i["scope_in"] or i["scope_out"]:
        L += ["", "## Scope"]
        if i["scope_in"]:
            L += ["**In:**", *bullets(i["scope_in"])]
        if i["scope_out"]:
            L += ["", "**Out:**", *bullets(i["scope_out"])]
    if i["notes"]:
        L += ["", "## Implementation notes", *bullets(i["notes"])]
    L += ["", "## Acceptance criteria", *bullets(i["criteria"], box=True)]
    L += ["", "## How to verify", *bullets(i["checks"] + common_checks(i), box=True)]
    L += ["", "## Live Qloo calls", i["calls"] if i["calls"] != "0" else "None expected."]
    if i["risks"]:
        L += ["", "## Risks and gotchas", *bullets(i["risks"])]
    L += ["", "## Blocked by"]
    L += [f"- #{num[d]} {by[d]['title']}" for d in i["deps"]] or ["- Nothing; can start now"]
    if blocks[i["key"]]:
        L += ["", "## Unblocks"]
        L += [f"- #{num[b]} {by[b]['title']}" for b in sorted(blocks[i["key"]], key=lambda k: num.get(k, 0))]
    if i["refs"]:
        L += ["", "## References", *bullets(i["refs"])]
    L += ["", "---"]
    if "needs-user" in labs:
        L.append("Maintainer task. Close with a comment showing the evidence.")
    else:
        L.append(
            "Follow `AGENTS.md` (definition of done: Section 14). Branch `p<N>/<topic>`, open a PR that "
            f"says `Closes #{num[i['key']]}`, tick these boxes in the PR, and stop for review."
        )
    L.append("Order and dependencies for every issue: `docs/issues-guide.md`.")
    return "\n".join(L)


if "--dry" in sys.argv:
    known = json.load(open(f"{HERE}/issue_numbers.json"))["num"]
    nums = {k: known.get(k, 900 + n) for n, k in enumerate(by)}
    for key in sys.argv[sys.argv.index("--dry") + 1 :]:
        print(body_for(by[key], nums), "\n" + "=" * 80)
    sys.exit(0)

# labels and milestones
existing = {lab["name"] for lab in paged("/labels")}
for name, (color, desc) in s.LABELS.items():
    if name in existing:
        api("PATCH", f"/labels/{name.replace(':', '%3A')}", {"color": color, "description": desc})
    else:
        api("POST", "/labels", {"name": name, "color": color, "description": desc})
ms = {m["title"]: m["number"] for m in paged("/milestones?state=all")}
for title, due, desc in s.MILESTONES:
    payload = {"title": title, "description": desc}
    if due:
        payload["due_on"] = f"{due}T18:00:00Z"
    if title in ms:
        api("PATCH", f"/milestones/{ms[title]}", payload)
    else:
        ms[title] = api("POST", "/milestones", payload)["number"]
print("labels and milestones ok")

# find existing issues by key, then title
current = [x for x in paged("/issues?state=all") if "pull_request" not in x]
by_key, by_title = {}, {x["title"]: x for x in current}
for x in current:
    m = re.search(r"\*\*Key:\*\* `([A-Z0-9-]+)`", x.get("body") or "")
    if m:
        by_key[m.group(1)] = x

num, iid = {}, {}
# pass 1: make sure every issue exists, so bodies can reference numbers
for i in s.ISSUES:
    ex = by_key.get(i["key"]) or by_title.get(i["title"])
    if ex:
        num[i["key"]], iid[i["key"]] = ex["number"], ex["id"]
    else:
        r = api(
            "POST",
            "/issues",
            {
                "title": i["title"],
                "body": "(being written)",
                "labels": labels_for(i),
                "milestone": ms[i["ms"]],
            },
        )
        num[i["key"]], iid[i["key"]] = r["number"], r["id"]
        print(f"created #{r['number']} {i['key']}")
        time.sleep(1.5)

# pass 2: full bodies, labels, milestone
for i in s.ISSUES:
    api(
        "PATCH",
        f"/issues/{num[i['key']]}",
        {"title": i["title"], "body": body_for(i, num), "labels": labels_for(i), "milestone": ms[i["ms"]]},
    )
    time.sleep(0.4)
print("bodies updated", len(s.ISSUES))

# pass 3: dependency links match the spec exactly
added = removed = 0
id_to_key = {v: k for k, v in iid.items()}
for i in s.ISSUES:
    k = i["key"]
    have = {d["id"] for d in (api("GET", f"/issues/{num[k]}/dependencies/blocked_by", ok_fail=True) or [])}
    want = {iid[d] for d in i["deps"]}
    for d in want - have:
        if api("POST", f"/issues/{num[k]}/dependencies/blocked_by", {"issue_id": d}, ok_fail=True) is None:
            print(f"link failed {k} <- {id_to_key.get(d)}")
        else:
            added += 1
    for d in have - want:
        if api("DELETE", f"/issues/{num[k]}/dependencies/blocked_by/{d}", ok_fail=True) is not None:
            removed += 1
print(f"links added {added}, removed {removed}")
json.dump({"num": num}, open(f"{HERE}/issue_numbers.json", "w"), indent=1)
json.dump(sorted(ZERO_SLACK), open(f"{HERE}/critical.json", "w"))
print("zero slack:", len(ZERO_SLACK))
