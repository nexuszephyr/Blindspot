"""Target models (#27): the agents Blindspot audits, behind one small protocol.

`Target.recommend(prompt, k, *, scenario_id)` returns a `TargetResponse` with a typed outcome and
never raises for a model or endpoint failure. `OpenAICompatTarget` speaks to any OpenAI-compatible
chat endpoint configured in `config/targets.yaml`; `ResponseCache` makes a repeat free. Model text
is untrusted data (rule 10), and nothing here reaches ground truth or a score (rule 8).
`CalibrationTarget` (#30) is four built-in targets with known answers, read offline from stored
answer keys; `open_target` picks any target, configured or `calib:...`, by id.
"""

from .cache import ResponseCache, prompt_hash
from .calibration import CALIBRATION_IDS, CalibrationTarget
from .client import OpenAICompatTarget
from .config import TargetConfig, env_lookup, get_target, load_targets
from .models import Outcome, Recommendation, Target, TargetResponse, Usage
from .registry import open_target, target_ids

__all__ = [
    "CALIBRATION_IDS",
    "CalibrationTarget",
    "OpenAICompatTarget",
    "Outcome",
    "Recommendation",
    "ResponseCache",
    "Target",
    "TargetConfig",
    "TargetResponse",
    "Usage",
    "env_lookup",
    "get_target",
    "load_targets",
    "open_target",
    "prompt_hash",
    "target_ids",
]
