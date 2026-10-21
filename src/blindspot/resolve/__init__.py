"""Entity resolver (#29): an agent's free-text picks -> Qloo entities, with status, method and
confidence, and the match rate per domain and script. Design: architecture Section 8.2, D47."""

from .match import FUZZY_THRESHOLD, token_set_ratio
from .models import Resolution, Resolved
from .report import MatchRate, format_rates, match_rates, script_of
from .resolver import Resolver

__all__ = [
    "FUZZY_THRESHOLD",
    "MatchRate",
    "Resolution",
    "Resolved",
    "Resolver",
    "format_rates",
    "match_rates",
    "script_of",
    "token_set_ratio",
]
