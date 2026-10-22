"""Target models as config (`config/targets.yaml`, #27, D49), and the env lookup for their values.

The file holds env variable *names* for the base URL and the key, never values. A misspelled or
unknown field fails to load (`extra=forbid`, as in `catalog.py`), so a typo can't silently send
`seed` to a target that rejects it.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator

from ..catalog import ConfigError, _load, _Strict

ENV_NAME = r"^[A-Z][A-Z0-9_]*$"
# Request fields the client sets itself; `extra_body` may not override them.
RESERVED_BODY_KEYS = frozenset(
    {"model", "messages", "temperature", "max_tokens", "seed", "response_format", "stream", "n", "tools"}
)

EnvLookup = Callable[[str], str]


class TargetConfig(_Strict):
    """One OpenAI-compatible chat model. Quirks per target come from D49."""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_\-]*$")
    display_name: str = Field(min_length=1)
    base_url_env: str = Field(pattern=ENV_NAME)
    base_url_default: str = ""  # a public endpoint, used when the base-URL variable is unset
    api_key_env: str = Field(pattern=ENV_NAME)
    model: str = Field(min_length=1)  # the id sent in the request
    live_in_demo: bool = False  # called live from the hosted demo (behind #38's caps)
    tool_calling: bool = False
    fix_loop_agent: bool = False  # the grounded agent in #42/#43; exactly one target
    max_tokens: int = Field(ge=1000, le=65536)  # reasoning tokens count against it (D49)
    seed: bool = True  # send `seed`; Gemini rejects it with a 400 (D49)
    json_mode: bool = True  # send `response_format: json_object`; parsing never depends on it
    extra_body: dict[str, Any] = Field(default_factory=dict)  # merged into the request (D49)
    timeout_s: float = Field(default=20.0, gt=0, le=300)
    max_concurrency: int = Field(default=2, ge=1, le=16)  # requests in flight to this target

    @field_validator("base_url_default")
    @classmethod
    def _public_https(cls, v: str) -> str:
        if not v:
            return v
        u = urlsplit(v)
        if u.scheme != "https" or not u.hostname or u.username or u.password or u.query or u.fragment:
            raise ValueError("must be a plain https URL (no credentials, query or fragment)")
        return v

    @field_validator("extra_body")
    @classmethod
    def _no_overrides(cls, v: dict[str, Any]) -> dict[str, Any]:
        bad = sorted(RESERVED_BODY_KEYS & v.keys())
        if bad:
            raise ValueError(f"{bad} are set by the client; use the matching field instead")
        return v

    @model_validator(mode="after")
    def _agent_calls_tools(self) -> TargetConfig:
        if self.fix_loop_agent and not self.tool_calling:
            raise ValueError("fix_loop_agent needs tool_calling: true")
        return self


def load_targets(config_dir: Path | str = "config") -> list[TargetConfig]:
    """`config/targets.yaml`, validated: unique ids and exactly one fix-loop agent."""
    path = Path(config_dir) / "targets.yaml"
    targets: list[TargetConfig] = _load(path, "targets", TargetConfig)
    if not targets:
        raise ConfigError(f"{path}: no targets")
    agents = [t.id for t in targets if t.fix_loop_agent]
    if len(agents) != 1:
        raise ConfigError(f"{path}: exactly one target needs fix_loop_agent: true; found {agents or 'none'}")
    return targets


def get_target(targets: list[TargetConfig], target_id: str) -> TargetConfig:
    for t in targets:
        if t.id == target_id:
            return t
    raise KeyError(f"unknown target {target_id!r}; known: {[t.id for t in targets]}")


def _dotenv(path: Path) -> dict[str, str]:
    """`KEY=value` lines of a .env file. Unquoted values end at ` #` (an inline comment)."""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        name, sep, value = line.strip().removeprefix("export ").partition("=")
        if not sep or name.strip().startswith("#"):
            continue
        value = value.strip()
        if value[:1] in {"'", '"'} and value.endswith(value[0]) and len(value) >= 2:
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].split("\t#", 1)[0].strip()
        out[name.strip()] = value
    return out


def env_lookup(env: Mapping[str, str] | None = None, env_file: Path | str = ".env") -> EnvLookup:
    """Look a variable up in the process environment, then in `.env` (as `Settings` does).
    Pass `env` to use only that mapping (tests)."""
    if env is not None:
        return lambda name: (env.get(name) or "").strip()
    file_values = _dotenv(Path(env_file))
    return lambda name: (os.environ.get(name) or file_values.get(name) or "").strip()


__all__ = ["EnvLookup", "TargetConfig", "env_lookup", "get_target", "load_targets"]
