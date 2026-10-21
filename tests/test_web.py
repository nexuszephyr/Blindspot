"""The web UI shell (#47): routes, the CSP split, the data label, untrusted-text rules, the mock
envelope and packaging. Offline and keyless; the mock is synthetic and names no configured city."""

from __future__ import annotations

import fnmatch
import json
import shutil
import subprocess
import tomllib
from html.parser import HTMLParser
from pathlib import Path

import pytest

from blindspot.service.security import API_CSP, UI_CSP, security_headers
from blindspot.web import STATIC_DIR

ROOT = Path(__file__).resolve().parents[1]
LABELS = json.loads((STATIC_DIR / "labels.json").read_text(encoding="utf-8"))
ENVELOPE_KEYS = {"data", "served_from", "synthetic", "degraded_reason", "run_id"}


# --------------------------------------------------------------------------- routes and headers
async def test_ui_page_is_served_with_the_self_only_csp(harness):
    h = await harness(http_rate_per_minute=1)
    for _ in range(3):  # not under /api/: the per-IP limiter never applies
        r = await h.get("/")
        assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["content-security-policy"] == UI_CSP
    assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"
    assert 'id="data-label"' in r.text
    assert (await h.http.head("/")).status_code == 200  # uptime monitors use HEAD
    assert "access-control-allow-origin" not in r.headers  # same origin: ALLOWED_ORIGINS stays empty
    assert h.fake.count() == 0


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("/static/app.js", "text/javascript"),
        ("/static/labels.js", "text/javascript"),
        ("/static/app.css", "text/css"),
        ("/static/labels.json", "application/json"),
        ("/static/mock/scenarios.json", "application/json"),
    ],
)
async def test_static_assets_are_served_with_the_ui_csp(harness, path, kind):
    """Exact types: with nosniff a browser refuses a script served as text/plain (Windows registry)."""
    h = await harness()
    r = await h.get(path)
    assert r.status_code == 200 and r.headers["content-type"].split(";")[0] == kind
    assert r.headers["content-security-policy"] == UI_CSP
    assert r.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path", ["/api", "/api/qloo/status", "/api/scenarios?limit=1", "/healthz", "/nope"])
async def test_api_and_everything_else_keep_the_strict_csp(harness, path):
    h = await harness()
    r = await h.get(path)
    assert r.headers["content-security-policy"] == API_CSP == "default-src 'none'; frame-ancestors 'none'"


async def test_static_rejects_path_traversal(harness):
    h = await harness()
    for path in ("/static/../__init__.py", "/static/%2e%2e/__init__.py", "/static/"):
        r = await h.get(path)
        assert r.status_code == 404, path
        assert "STATIC_DIR" not in r.text


def test_csp_split_is_by_path():
    assert security_headers("/")["Content-Security-Policy"] == UI_CSP
    assert security_headers("/static/app.js")["Content-Security-Policy"] == UI_CSP
    for path in ("/api", "/api/", "/api/scenarios", "/healthz", "/staticx", "/static", "//static/x"):
        assert security_headers(path)["Content-Security-Policy"] == API_CSP, path


def test_ui_csp_allows_only_same_origin_and_no_inline():
    assert "unsafe-inline" not in UI_CSP and "unsafe-eval" not in UI_CSP and "*" not in UI_CSP
    directives = dict(d.strip().split(" ", 1) for d in UI_CSP.split(";"))
    assert directives == {
        "default-src": "'self'",
        "script-src": "'self'",
        "style-src": "'self'",
        "img-src": "'self' data:",
        "connect-src": "'self'",
        "frame-ancestors": "'none'",
        "base-uri": "'none'",
        "form-action": "'self'",
    }


# --------------------------------------------------------------------------- page rules (CSP, rule 10)
class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.problems: list[str] = []
        self.refs: list[str] = []
        self.ids: set[str] = set()
        self._in_script = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "id" in a:
            self.ids.add(a["id"] or "")
        for name, _ in attrs:
            if name.startswith("on") or name == "style":
                self.problems.append(f"<{tag} {name}=>")
        if tag == "style":
            self.problems.append("<style>")
        if tag == "script":
            if not a.get("src"):
                self.problems.append("inline <script>")
            self._in_script = True
        for name in ("src", "href"):
            if a.get(name) and tag in ("script", "link", "img"):
                self.refs.append(a[name] or "")

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_script = False

    def handle_data(self, data):
        if self._in_script and data.strip():
            self.problems.append("script body")


def _page() -> _Page:
    p = _Page()
    p.feed((STATIC_DIR / "index.html").read_text(encoding="utf-8"))
    return p


def test_page_has_no_inline_script_style_or_handlers():
    assert _page().problems == []


def test_page_assets_are_same_origin_and_exist():
    for ref in _page().refs:
        if ref.startswith("data:"):
            continue
        assert ref.startswith("/static/"), ref  # no CDN, no other origin
        assert (STATIC_DIR / ref.removeprefix("/static/")).is_file(), ref


def test_page_has_the_label_and_the_cold_start_state():
    ids = _page().ids
    assert {"data-label", "wake", "wake-text", "wake-retry", "main"} <= ids
    js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert '"/healthz"' in js and "Waking up the demo" in js


@pytest.mark.parametrize("name", ["app.js", "labels.js"])
def test_scripts_never_write_html(name):
    js = (STATIC_DIR / name).read_text(encoding="utf-8")
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert bad not in js, f"{name} uses {bad}"
    assert ".style." not in js and 'setAttribute("style"' not in js  # CSP: no inline style


def test_css_has_tokens_dark_mode_gutters_and_focus():
    css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
    assert "@media (prefers-color-scheme: dark)" in css
    assert "--gutter: 16px" in css and "padding-inline: var(--gutter)" in css
    assert ":focus-visible" in css


# --------------------------------------------------------------------------- the data label (rule 5)
def test_label_table_never_says_live_for_stored_data():
    rows = LABELS["served_from"]
    assert set(rows) == {"live", "cache", "snapshot", "disabled", "degraded"}
    assert rows["live"]["text"] == "Live Qloo data"
    lives = [k for k, v in rows.items() if "live" in v["text"].lower()]
    assert lives == ["live"]
    for other in ("synthetic", "no_qloo_route_disabled", "unknown"):
        assert "live" not in LABELS[other]["text"].lower()
    assert rows["cache"]["text"] == rows["snapshot"]["text"] == "Qloo data (stored)"
    assert LABELS["synthetic"]["text"] == "Synthetic sample data"
    assert rows["disabled"]["text"] == "Qloo off" and rows["degraded"]["text"] == "Qloo unavailable"
    assert LABELS["no_qloo_route_disabled"]["text"] != rows["disabled"]["text"]  # D45: not the kill switch


LABEL_CASES = [
    ({"served_from": "live", "synthetic": False}, {}, "Live Qloo data"),
    ({"served_from": "cache", "synthetic": False}, {}, "Qloo data (stored)"),
    ({"served_from": "snapshot", "synthetic": False}, {}, "Qloo data (stored)"),
    ({"served_from": "live", "synthetic": True}, {}, "Synthetic sample data"),
    ({"served_from": "snapshot", "synthetic": True}, {}, "Synthetic sample data"),
    ({"served_from": "disabled", "synthetic": True}, {"noQlooRoute": True}, "Synthetic sample data"),
    ({"served_from": "disabled", "synthetic": False}, {}, "Qloo off"),
    ({"served_from": "disabled", "synthetic": False}, {"noQlooRoute": True}, "No Qloo data"),
    (
        {"served_from": "degraded", "degraded_reason": "answer key not built"},
        {},
        "Qloo unavailable: answer key not built",
    ),
    ({"served_from": "degraded", "degraded_reason": None}, {}, "Qloo unavailable"),
    ({"served_from": "cache", "degraded_reason": "ignored"}, {}, "Qloo data (stored)"),
    ({"served_from": "LIVE"}, {}, "Data source unknown"),
    ({"served_from": "live "}, {}, "Data source unknown"),
    ({"served_from": "toString"}, {}, "Data source unknown"),
    ({"served_from": "__proto__"}, {}, "Data source unknown"),
    ({"synthetic": False}, {}, "Data source unknown"),
    ({}, {}, "Data source unknown"),
    (None, {}, "Data source unknown"),
    ("live", {}, "Data source unknown"),
]

_NODE_RUNNER = r"""
const fs = require("fs"), vm = require("vm");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const ctx = {};
vm.runInNewContext(fs.readFileSync(input.script, "utf8"), ctx);
const out = input.cases.map(([env, opts]) => ctx.BlindspotLabels.dataLabel(env, input.table, opts));
process.stdout.write(JSON.stringify(out));
"""


def test_label_function_matches_the_rules():
    """Runs the UI's own labels.js under Node (present on the CI runners); skipped without it."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    payload = {
        "script": str(STATIC_DIR / "labels.js"),
        "table": LABELS,
        "cases": [[env, opts] for env, opts, _ in LABEL_CASES],
    }
    run = subprocess.run(  # noqa: S603 - fixed argv, our own script, no shell
        [node, "-e", _NODE_RUNNER],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=True,
    )
    got = json.loads(run.stdout)
    assert [g["text"] for g in got] == [want for _, _, want in LABEL_CASES]
    tones = {r["tone"] for r in LABELS["served_from"].values()} | {
        LABELS[k]["tone"] for k in ("synthetic", "no_qloo_route_disabled", "unknown")
    }
    assert {g["tone"] for g in got} <= tones


# --------------------------------------------------------------------------- the mock envelope
def test_mock_is_a_synthetic_envelope_shaped_like_the_scenario_route():
    from blindspot.catalog import load_catalog
    from blindspot.idlock import load_lock
    from blindspot.scenarios.generator import generate
    from blindspot.service.scenario_routes import _summary

    mock = json.loads((STATIC_DIR / "mock" / "scenarios.json").read_text(encoding="utf-8"))
    assert set(mock) == ENVELOPE_KEYS
    assert mock["synthetic"] is True and mock["served_from"] == "disabled"
    assert mock["run_id"].startswith("run_")
    assert set(mock["data"]) == {"items", "total", "limit", "offset"}
    real = _summary(generate(load_catalog(ROOT / "config"), load_lock(ROOT / "config")).scenarios[0])
    for item in mock["data"]["items"]:
        assert set(item) == set(real)
        assert item["id"].startswith("sc_") and item["city_id"].startswith("sample_")
        assert item["persona_id"].startswith("sample_")


def test_mock_names_no_configured_city_or_persona():
    from blindspot.catalog import load_catalog

    cat = load_catalog(ROOT / "config")
    text = (STATIC_DIR / "mock" / "scenarios.json").read_text(encoding="utf-8").lower()
    words = [c.id for c in cat.cities] + [c.display_name for c in cat.cities] + [p.id for p in cat.personas]
    for word in words:
        assert f'"{word.lower()}"' not in text, word


# --------------------------------------------------------------------------- packaging
def test_every_static_file_ships_as_package_data():
    """The Dockerfile copies only src/ and config/ and installs the package: anything the
    package-data globs miss would be absent from the image."""
    globs = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["setuptools"][
        "package-data"
    ]["blindspot.web"]
    pkg = STATIC_DIR.parent
    files = [p.relative_to(pkg).as_posix() for p in STATIC_DIR.rglob("*") if p.is_file()]
    assert files
    for f in files:
        assert any(fnmatch.fnmatch(f, g) and f.count("/") == g.count("/") for g in globs), f
