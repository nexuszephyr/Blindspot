"""Target models (#27): the agents Blindspot audits, behind one small protocol.

`Target.recommend(prompt, k, *, scenario_id)` returns a `TargetResponse` with a typed outcome and
never raises for a model or endpoint failure. `OpenAICompatTarget` speaks to any OpenAI-compatible
chat endpoint configured in `config/targets.yaml`; `ResponseCache` makes a repeat free. Model text
is untrusted data (rule 10), and nothing here reaches ground truth or a score (rule 8).
"""

from .cache import ResponseCache, prompt_hash
from .client import OpenAICompatTarget
from .config import TargetConfig, env_lookup, get_target, load_targets
from .models import Outcome, Recommendation, Target, TargetResponse, Usage

__all__ = [
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
    "prompt_hash",
]
