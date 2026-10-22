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


def test_target_model_keys_are_scanned():
    # #27: the Groq and Gemini keys, by value and by their well-known shapes
    assert {"GROQ_API_KEY", "GEMINI_API_KEY"} <= set(S["SECRET_VARS"])
    groq = "gs" + "k_" + "A1b2C3d4" * 6
    google = "AI" + "za" + "B" * 35
    assert any("groq-api-key" in f for f in scan(f"key = {groq}\n"))
    assert any("google-api-key" in f for f in scan(f"url?key={google}\n"))
