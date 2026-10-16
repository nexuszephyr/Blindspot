"""Create labels, milestones, issues and dependency links. Idempotent by title."""
import importlib.util
import json
import subprocess
import sys
import time

REPO = "repos/nexuszephyr/Blindspot"
HERE = sys.argv[1]
spec = importlib.util.spec_from_file_location("s", f"{HERE}/issues_spec.py")
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)


def api(method, path, payload=None, ok_fail=False):
    cmd = ["gh", "api", "-X", method, f"{REPO}{path}", "-H", "Accept: application/vnd.github+json"]
    if payload is not None:
        cmd += ["--input", "-"]
    r = subprocess.run(cmd, input=json.dumps(payload) if payload is not None else None,
                       capture_output=True, text=True)
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


# labels
existing = {lab["name"] for lab in paged("/labels")}
for name, (color, desc) in s.LABELS.items():
    if name in existing:
        api("PATCH", f"/labels/{name.replace(':', '%3A')}", {"color": color, "description": desc})
    else:
        api("POST", "/labels", {"name": name, "color": color, "description": desc})
print("labels ok")

# milestones
ms = {m["title"]: m["number"] for m in paged("/milestones?state=all")}
for title, due, desc in s.MILESTONES:
    body = {"title": title, "description": desc}
    if due:
        body["due_on"] = f"{due}T18:00:00Z"
    if title in ms:
        api("PATCH", f"/milestones/{ms[title]}", body)
    else:
        ms[title] = api("POST", "/milestones", body)["number"]
print("milestones ok")

# topological order
by = {i["key"]: i for i in s.ISSUES}
order, seen = [], set()


def visit(k):
    if k in seen:
        return
    for d in by[k]["deps"]:
        visit(d)
    seen.add(k)
    order.append(k)


for i in s.ISSUES:
    visit(i["key"])

issues = {i["title"]: i for i in paged("/issues?state=all") if "pull_request" not in i}
num, iid = {}, {}


def body_for(i):
    lines = [f"**Key:** `{i['key']}`", "", "## Goal", i["goal"], "", "## Acceptance criteria"]
    lines += [f"- [ ] {c}" for c in i["criteria"]]
    if i["deps"]:
        lines += ["", "## Blocked by"]
        lines += [f"- #{num[d]} {by[d]['title']}" for d in i["deps"]]
    if i["refs"]:
        lines += ["", "## References"] + [f"- {r}" for r in i["refs"]]
    lines += ["", "---", "Order and dependencies for every issue: `docs/issues-guide.md`. "
              "Follow `AGENTS.md`; work on a branch and open a PR that says `Closes #<this issue>`."]
    return "\n".join(lines)


for k in order:
    i = by[k]
    payload = {"title": i["title"], "body": body_for(i), "labels": i["labels"],
               "milestone": ms[i["ms"]]}
    if i["title"] in issues:
        ex = issues[i["title"]]
        api("PATCH", f"/issues/{ex['number']}", payload)
        num[k], iid[k] = ex["number"], ex["id"]
    else:
        r = api("POST", "/issues", payload)
        num[k], iid[k] = r["number"], r["id"]
        time.sleep(1.5)
    print(f"#{num[k]} {k}")

# dependency links
links = 0
for k in order:
    have = {d["id"] for d in (api("GET", f"/issues/{num[k]}/dependencies/blocked_by", ok_fail=True) or [])}
    for d in by[k]["deps"]:
        if iid[d] in have:
            continue
        if api("POST", f"/issues/{num[k]}/dependencies/blocked_by", {"issue_id": iid[d]}, ok_fail=True) is None:
            print(f"link failed {k} <- {d}")
        else:
            links += 1
print("links added", links)
json.dump({"num": num}, open(f"{HERE}/issue_numbers.json", "w"), indent=1)
