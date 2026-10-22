"""The fix loop (#42, Phase 5): the grounded agent's Qloo tools and its tool-calling loop.

`QlooTools` runs `qloo_search` and `qloo_recommend` through `QlooClient` (budgets, deadline, cache,
call log); `run_grounded` drives a tool-calling target over them. The tools can't reproduce an
answer-key query (the honesty rule, D52). The grounded wrapper and the re-run are #43.
"""

from .loop import Caps, GroundedResponse, ToolCallRecord, run_grounded
from .tools import QlooTools, ToolResult, assert_honest, query_shape, tool_definitions

__all__ = [
    "Caps",
    "GroundedResponse",
    "QlooTools",
    "ToolCallRecord",
    "ToolResult",
    "assert_honest",
    "query_shape",
    "run_grounded",
    "tool_definitions",
]
