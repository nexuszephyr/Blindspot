"""#78: the project secret scanner must find what it exists to find.

Planted values are built at runtime so neither this scanner nor gitleaks flags this file.
"""

import runpy
from pathlib import Path

S = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts" / "scan_secrets.py"), run_name="scan")
FAKE_VALUE = "fake" + "-qloo-" + "value-0123456789"
ENV_LINE = "X_API_" + "KEY=" + "q7Lm2Xp9Rt4Vw8Zb"


def scan(data: str, secrets: dict[str, bytes] | None = None) -> list[str]:
    findings: list[str] = []
    S["scan_bytes"](data.encode(), "sample.txt", secrets or {}, findings)
    return findings


def test_finds_the_literal_key_value():
    found = scan(f"debug: sending {FAKE_VALUE} upstream\n", {"QLOO_API_KEY": FAKE_VALUE.encode()})
    assert any("value of QLOO_API_KEY" in f for f in found)


def test_finds_an_unquoted_env_style_key():
    found = scan(f"# config dump\n{ENV_LINE}\n")
    assert any("env-style-secret" in f for f in found)


def test_clean_input_finds_nothing():
    assert scan("QLOO_API_KEY=\nQLOO_RPS=4\nprint('hello')\n", {"QLOO_API_KEY": FAKE_VALUE.encode()}) == []
