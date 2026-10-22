"""One lookup for every target id (#30): a configured model from `config/targets.yaml`, or one of
the built-in calibration targets (`calib:...`). Callers pick a target by id and get a `Target`."""

from __future__ import annotations

from typing import Any

from ..catalog import Catalog
from ..scenarios.generator import DEFAULT_SEED
from ..scenarios.models import Scenario
from .cache import ResponseCache
from .calibration import CALIBRATION_IDS, CalibrationTarget
from .client import OpenAICompatTarget
from .config import EnvLookup, TargetConfig, get_target
from .models import Target


def target_ids(configs: list[TargetConfig]) -> list[str]:
    """Every selectable id: the configured models, then the calibration targets."""
    return [t.id for t in configs] + list(CALIBRATION_IDS)


def open_target(
    target_id: str,
    configs: list[TargetConfig],
    *,
    cache: ResponseCache | None = None,
    env: EnvLookup | None = None,
    catalog: Catalog | None = None,
    scenarios: list[Scenario] | None = None,
    store: Any = None,
    seed: int = DEFAULT_SEED,
) -> Target:
    """The target with this id. A calibration target needs `catalog` and `scenarios` (and reads the
    answer-key `store`); a configured one uses `cache` and `env`. Raises KeyError for an unknown id."""
    if target_id in CALIBRATION_IDS:
        if catalog is None or scenarios is None:
            raise ValueError("calibration targets need the catalog and the scenarios")
        return CalibrationTarget(target_id, catalog=catalog, scenarios=scenarios, store=store, seed=seed)
    try:
        cfg = get_target(configs, target_id)
    except KeyError:
        raise KeyError(f"unknown target {target_id!r}; known: {target_ids(configs)}") from None
    return OpenAICompatTarget(cfg, cache, env=env)


__all__ = ["open_target", "target_ids"]
