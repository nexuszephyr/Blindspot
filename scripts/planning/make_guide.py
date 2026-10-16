import importlib.util
import itertools
import json
import sys

HERE, OUT = sys.argv[1], sys.argv[2]
spec = importlib.util.spec_from_file_location("s", f"{HERE}/issues_spec.py")
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)
num = json.load(open(f"{HERE}/issue_numbers.json"))["num"]
by = {i["key"]: i for i in s.ISSUES}
URL = "https://github.com/nexuszephyr/Blindspot/issues/"


def ref(k):
    return f"[#{num[k]}]({URL}{num[k]})"


def size(i):
    return next(lab for lab in i["labels"] if lab.startswith("size:"))[5:]


def prio(i):
    return next(lab for lab in i["labels"] if lab in ("P0", "P1", "P2"))


def short(t):
    for p in ("Phase 2: ", "Phase 3: ", "Phase 4: ", "Phase 5: ", "Phase 6: ", "Phase 7: ", "Stretch: "):
        t = t.replace(p, "")
    return t[:1].upper() + t[1:]


blocks = {k: [] for k in by}
for i in s.ISSUES:
    for d in i["deps"]:
        blocks[d].append(i["key"])

memo = {}


def depth(k):
    if k not in memo:
        memo[k] = 1 + max([depth(d) for d in by[k]["deps"]] or [0])
    return memo[k]


crit, k = [], "S2"
while True:
    crit.append(k)
    if not by[k]["deps"]:
        break
    k = max(by[k]["deps"], key=depth)
crit.reverse()

total = depth("S2")
hmemo = {}


def height(k):
    # nodes on the longest path from k to S2 (inclusive); 0 if S2 is unreachable
    if k not in hmemo:
        if k == "S2":
            hmemo[k] = 1
        else:
            hs = [height(b) for b in blocks[k]]
            best = max([h for h in hs if h] or [0])
            hmemo[k] = best + 1 if best else 0
    return hmemo[k]


zero_slack = {k for k in by if height(k) and depth(k) + height(k) - 1 == total}
json.dump(sorted(zero_slack), open(f"{HERE}/critical.json", "w"))


def node(k):
    t = short(by[k]["title"]).replace('"', "'").replace("`", "")
    if len(t) > 38:
        t = t[:36].rstrip() + "…"
    return f'    {k.replace("-", "_")}["#{num[k]} {t}"]'


def graph(keys, extra_edges=True):
    keys = list(keys)
    ks = set(keys)
    lines = ["```mermaid", "flowchart LR"]
    ext = set()
    for k in keys:
        for d in by[k]["deps"]:
            if d not in ks:
                ext.add(d)
    for k in keys:
        lines.append(node(k))
    for d in sorted(ext, key=lambda x: num[x]):
        t = f"#{num[d]} ({d})"
        lines.append(f'    {d.replace("-", "_")}(["{t}"])')
    for k in keys:
        for d in by[k]["deps"]:
            lines.append(f"    {d.replace('-', '_')} --> {k.replace('-', '_')}")
    crit_in = [k for k in keys if k in zero_slack]
    if crit_in:
        lines.append("    classDef crit stroke-width:3px")
        lines.append("    class " + ",".join(k.replace("-", "_") for k in crit_in) + " crit")
    lines.append("```")
    return "\n".join(lines)


def table(keys):
    rows = ["| Order | Issue | Key | Title | Blocked by | Size | Priority |", "|---|---|---|---|---|---|---|"]
    keys = sorted(keys, key=lambda k: (depth(k), num[k]))
    for n, k in enumerate(keys, 1):
        i = by[k]
        deps = ", ".join(ref(d) for d in i["deps"]) or "nothing"
        star = " ⭐" if k in zero_slack else ""
        rows.append(
            f"| {n} | {ref(k)}{star} | `{k}` | {short(i['title'])} | {deps} | {size(i)} | {prio(i)} |"
        )
    return "\n".join(rows)


phases = [
    (
        "Maintainer actions",
        "Maintainer actions",
        "Only you can do these: accounts, dashboards, decisions. Start U1 to U4 today; they block later work.",
    ),
    ("Phase 2", "Phase 2: Answer key", "Build the exam and a validated Qloo answer key. Due 2026-10-18."),
    ("Phase 3", "Phase 3: Scoring", "Target adapters, resolver, metrics, calibration. Due 2026-10-20."),
    ("Phase 4", "Phase 4: Auditor agent", "The agent that finds and confirms blindspots. Due 2026-10-22."),
    ("Phase 5", "Phase 5: Fix loop", "Qloo-grounded re-run with honest before and after. Due 2026-10-23."),
    (
        "Phase 6",
        "Phase 6: Web UI",
        "Blind Map, Truth Table, Truth Receipts, kill switch. Due 2026-10-26. The scaffold (#{p61}) can start right after #{p211}, against mock data.",
    ),
    ("Phase 7", "Phase 7: Ship", "Precompute, hardening, findings, write-up, final QA. Due 2026-10-27."),
    ("Submission", "Submission", "Make the repo public and submit by 2026-10-28."),
    (
        "Backlog",
        "Backlog",
        "Stretch goals and deferred items. Only touch these when everything P0 and P1 is done.",
    ),
]

ready = [k for k in by if not by[k]["deps"] and by[k]["ms"] != "Backlog"]

out = []
w = out.append
w("# Issue guide: what to work on, in what order\n")
w(
    "Every planned task from now to submission is a GitHub issue. This guide says which to pick up first, "
    "what blocks what, and what can run in parallel. It's generated from the same data used to create the "
    "issues, so numbers match. GitHub also shows each issue's **Blocked by** and **Blocking** links in its sidebar.\n"
)
w(f"- **{len(s.ISSUES)} issues**, **{sum(len(i['deps']) for i in s.ISSUES)} dependency links**, 9 milestones")
w(
    "- Phase scope and exit criteria: [architecture.md, Section 19](architecture.md#19-phase-plan-what-each-phase-must-achieve)"
)
w("- Schedule and status: [roadmap.md](roadmap.md)\n")

w("## How to read the labels\n")
w("| Label | Meaning |\n|---|---|")
w("| `P0` | Critical path or required to submit; never cut |")
w("| `P1` | Needed for a strong submission; cut only in an emergency |")
w("| `P2` | Nice to have; first to cut |")
w(
    "| `critical-path` / ⭐ | Zero slack: on a longest dependency chain, so a delay here delays submission day |"
)
w("| `size:S` / `size:M` / `size:L` | Up to about 2 hours / half a day / a day |")
w("| `needs-user` | Only the maintainer can do it |")
w("| `stretch`, `deferred` | Backlog; not planned for the hackathon unless time is left |")
w("| `phase-N`, `ui`, `llm`, `qloo-api`, `security`, `judge-experience`, `docs` | Area |\n")

w("## Start here (unblocked right now)\n")
w("| Issue | Title | Who |\n|---|---|---|")
for k in sorted(ready, key=lambda k: num[k]):
    who = "Maintainer" if "needs-user" in by[k]["labels"] else "Claude Code / dev"
    w(f"| {ref(k)} | {short(by[k]['title'])} | {who} |")
w("")
w(
    f"Do {ref('U1')} (Render) and {ref('U4')} (target models and keys) first: hosting problems need time to surface, "
    f"and Phase 3 can't start without target models. On the code side, start {ref('P2-2')} and {ref('P2-1')} in parallel.\n"
)

w("## Critical path\n")
w(
    "The longest chain of dependencies from today to submission. Any slip on these issues slips the deadline; "
    "everything else has slack.\n"
)
w(" → ".join(f"{ref(k)}" for k in crit) + "\n")
others = sorted(zero_slack - set(crit), key=lambda k: num[k])
if others:
    w(
        "These are tied for longest, so they have zero slack too and carry ⭐ below: "
        + ", ".join(f"{ref(k)} {short(by[k]['title'])}" for k in others)
        + ".\n"
    )
w("| Step | Issue | Title | Size | Phase deadline |\n|---|---|---|---|---|")
dl = {m[0]: m[1] for m in s.MILESTONES}
for n, k in enumerate(crit, 1):
    w(f"| {n} | {ref(k)} | {short(by[k]['title'])} | {size(by[k])} | {dl[by[k]['ms']]} |")
w("")

w("```mermaid")
w("flowchart LR")
for k in crit:
    w(node(k))
for a, b in itertools.pairwise(crit):
    w(f"    {a.replace('-', '_')} --> {b.replace('-', '_')}")
w("```\n")

w("## Parallel tracks\n")
w(
    "If more than one person (or Claude Code session) is working, split like this. Each track only waits on "
    "the hand-offs listed.\n"
)
w("| Track | Issues | Waits on |\n|---|---|---|")
w("| A: Core pipeline | Phase 2 → 3 → 4 → 5 critical path | nothing; this is the critical path |")
w(
    f"| B: Hardening and API | {ref('P2-1')}, {ref('P2-10')}, {ref('P2-11')}, {ref('P2-12')}, {ref('P3-2')}, {ref('P4-4')} | {ref('P2-3')}, {ref('P2-7')} |"
)
w(
    f"| C: UI | {ref('P6-1')} on mock data, then the rest of Phase 6 | {ref('P2-11')} to start; {ref('P4-5')} for real data |"
)
w(
    f"| D: Maintainer | {', '.join(ref(k) for k in ['U1', 'U2', 'U3', 'U4', 'U5', 'U6'])}, {ref('S1')}, {ref('S2')} | {ref('P2-8')} and {ref('P2-12')} for U6 and U5 |"
)
w("")

w("## Day-by-day plan\n")
w("| Date | Aim |\n|---|---|")
plan = [
    (
        "Oct 16 (Fri)",
        f"Maintainer: {ref('U1')}, {ref('U4')}, {ref('U2')}, {ref('U3')}. Code: {ref('P2-2')}, {ref('P2-1')}",
    ),
    ("Oct 17 (Sat)", f"{ref('P2-3')} → {ref('P2-4')} → {ref('P2-5')} → {ref('P2-6')}; start {ref('P2-7')}"),
    (
        "Oct 18 (Sun)",
        f"{ref('P2-7')}, {ref('P2-8')}, {ref('P2-9')}; side: {ref('P2-10')}, {ref('P2-11')}, {ref('P2-12')}; maintainer reviews {ref('U6')}",
    ),
    ("Oct 19 (Mon)", f"{ref('P3-1')}, {ref('P3-3')}, {ref('P3-4')}; UI track starts {ref('P6-1')}"),
    ("Oct 20 (Tue)", f"{ref('P3-5')}, {ref('P3-6')}, {ref('P3-7')}, {ref('P3-2')}"),
    ("Oct 21 (Wed)", f"{ref('P4-1')}, {ref('P4-2')}"),
    ("Oct 22 (Thu)", f"{ref('P4-3')}, {ref('P4-4')}, {ref('P4-5')}; {ref('P5-1')} in parallel"),
    ("Oct 23 (Fri)", f"{ref('P5-2')}, {ref('P5-3')}; UI wires to real audits: {ref('P6-3')}"),
    ("Oct 24 (Sat)", f"{ref('P6-2')}, {ref('P6-4')}, {ref('P6-5')}, {ref('P6-6')}"),
    ("Oct 25 (Sun)", f"{ref('P6-8')}, {ref('P6-7')}, {ref('P6-9')}"),
    ("Oct 26 (Mon)", f"{ref('P6-10')} stranger test; {ref('P7-1')} precompute"),
    ("Oct 27 (Tue)", f"{ref('P7-2')}, {ref('P7-3')}, {ref('P7-5')}, {ref('P7-6')}, {ref('P7-7')}"),
    ("Oct 28 (Wed)", f"{ref('S1')} make public, {ref('S2')} submit"),
    ("Oct 29 to 30", "Buffer only. Devpost closes Oct 30."),
]
for d, a in plan:
    w(f"| {d} | {a} |")
w("")

w("## Issues by phase\n")
w(
    "Within each phase, the order column is a valid working order: every issue comes after the issues that block it.\n"
)
p61, p211 = num["P6-1"], num["P2-11"]
for heading, msname, blurb in phases:
    keys = [k for k in by if by[k]["ms"] == msname]
    w(f"### {heading}\n")
    w(blurb.format(p61=p61, p211=p211) + "\n")
    w(table(keys) + "\n")
    if heading not in ("Backlog",) and len(keys) > 1:
        w(graph(sorted(keys, key=lambda k: num[k])) + "\n")

w("## If you fall behind\n")
w("Cut in this order; each line is safe to drop without breaking anything after it:\n")
cuts = [
    ("P7-8", "Score-regression Action and badge"),
    ("P7-4", "Blind user test"),
    ("P6-7", "Live trace and call ticker"),
    ("P5-5", f"Full MCP server (keep the exported config from #{num['P5-3']})"),
    ("P6-9", "Try your own city"),
    ("P7-3", "Findings write-up (keep a short version in the Devpost text)"),
]
for n, (k, t) in enumerate(cuts, 1):
    w(f"{n}. {ref(k)} {t}")
w("")
w(
    f"Never cut: calibration ({ref('P3-6')}), Truth Table ({ref('P6-4')}), Truth Receipts ({ref('P6-5')}), "
    f"kill switch ({ref('P6-6')}), hosted demo ({ref('U1')}), final QA ({ref('P7-7')}).\n"
)

w("## Working an issue\n")
w(
    "1. Pick the lowest-numbered unblocked issue in your track (check its **Blocked by** links are all closed)."
)
w("2. Assign yourself; branch `p<N>/<topic>` (or `fix/`, `docs/`, `chore/`).")
w(
    '3. Follow [AGENTS.md](../AGENTS.md). For Claude Code, a good prompt is: *"Work on issue #NN. Follow AGENTS.md '
    "and the issue's acceptance criteria. Open a PR that says `Closes #NN` and stop for review.\"*"
)
w("4. Tick the acceptance criteria in the PR description, not just the issue.")
w("5. When a phase's last issue closes, update [roadmap.md](roadmap.md) and the phase report.\n")
w("## Keeping this guide current\n")
w(
    "The issue set and this file come from one spec. If issues are added or re-linked on GitHub, update this "
    "file in the same PR so the order and critical path stay true."
)

open(OUT, "w").write("\n".join(out) + "\n")
print("written", OUT)
