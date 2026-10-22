"""`blindspot target list|ask` (#27). For the maintainer's local use; never commit its output.

    blindspot target list
    blindspot target ask --target <id> --scenario <scenario id> [--variant N] [--no-cache]

`list` shows each target and whether its key variable is set (never the value). `ask` sends one of
the scenario's prompt variants to the target (a live model call unless the answer is cached) and
prints the outcome, the token usage and the parsed names.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from ..config import get_settings
from ..console import utf8_console
from ..scenarios.cli import OUT_REL, load_set
from .cache import ResponseCache
from .client import OpenAICompatTarget
from .config import env_lookup, get_target, load_targets


def _out(text: str) -> None:
    print(text)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="blindspot target", description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--config", type=Path, default=Path("config"), help="config dir; its parent is the repo root"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="targets and whether their key is set")
    ask = sub.add_parser("ask", help="ask a target one scenario's prompt")
    ask.add_argument("--target", required=True)
    ask.add_argument("--scenario", required=True)
    ask.add_argument("--variant", type=int, default=0, help="index of the prompt variant (default 0)")
    ask.add_argument("--file", type=Path, help=f"scenario file (default: <repo>/{OUT_REL.as_posix()})")
    ask.add_argument("--no-cache", action="store_true", help="skip the response cache (a live call)")
    a = ap.parse_args(argv)
    utf8_console()
    root = a.config.resolve().parent
    targets = load_targets(a.config)
    env = env_lookup(env_file=root / ".env")

    if a.cmd == "list":
        for t in targets:
            # Only whether the variable is set ever leaves this loop: the value is reduced to a bool
            # here and nothing derived from it is printed (CodeQL py/clear-text-logging).
            configured = bool(env(t.api_key_env))
            state = "configured" if configured else f"{t.api_key_env} not set"
            flags = [f for f in ("live_in_demo", "tool_calling", "fix_loop_agent") if getattr(t, f)]
            _out(f"{t.id:<14} {t.model:<24} {state:<22} {','.join(flags)}")
        return 0

    try:
        cfg = get_target(targets, a.target)
    except KeyError as exc:
        _out(str(exc.args[0]))
        return 2
    match = [s for s in load_set(a.file or root / OUT_REL) if s.id == a.scenario]
    if not match:
        _out(f"no scenario {a.scenario!r}")
        return 1
    sc = match[0]
    if not 0 <= a.variant < len(sc.prompt_variants):
        _out(f"--variant must be 0 to {len(sc.prompt_variants) - 1}")
        return 2
    path = get_settings().target_cache_path
    cache = None if a.no_cache else ResponseCache(path if path.is_absolute() else root / path)
    target = OpenAICompatTarget(cfg, cache, env=env)

    async def run():
        try:
            return await target.recommend(sc.prompt_variants[a.variant].text, sc.k, scenario_id=sc.id)
        finally:
            await target.aclose()

    r = asyncio.run(run())
    u = r.usage
    _out(f"{r.target_id} ({r.model}): {r.outcome} via {r.parse_method}, served_from {r.served_from}")
    _out(f"finish {r.finish_reason}, {r.latency_ms:.0f} ms, {u.calls} call(s)")
    _out(
        f"tokens: prompt {u.prompt_tokens}, completion {u.completion_tokens}, "
        f"reasoning {u.reasoning_tokens}, total {u.total_tokens}"
    )
    if r.detail:
        _out(f"detail: {r.detail}")
    for i, it in enumerate(r.items, 1):
        _out(f"{i:>2}. {it.name}")
    if cache is not None:
        cache.close()
    return 0 if r.outcome == "ok" else 1


__all__ = ["main"]
