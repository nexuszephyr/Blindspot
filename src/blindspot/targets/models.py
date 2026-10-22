"""What a target returns (#27). Model text is untrusted (rule 10): it is data, never executed, never
interpolated into SQL or shell, and escaped wherever it is shown."""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

# ok: 1 to k items parsed. empty: the model answered with nothing (or an empty list).
# malformed: text that didn't parse into any item (truncated output included). refused: the model
# declined. timeout / error: no usable answer from the endpoint (not configured, HTTP error, bad body).
Outcome = Literal["ok", "empty", "malformed", "refused", "timeout", "error"]
ParseMethod = Literal["json", "partial", "repair", "lines", "none"]


class Recommendation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=200)
    why: str = Field(default="", max_length=500)


class Usage(BaseModel):
    """Token counts as the provider reports them, summed over the calls one answer took (a repair
    retry is a second call). `reasoning_tokens` is GPT-OSS's hidden part (D49); Gemini doesn't
    report its own, so `total - prompt - completion` is the hidden part there."""

    model_config = ConfigDict(extra="forbid")

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int = 0
    calls: int = 0  # chat requests that returned a body

    def __add__(self, other: Usage) -> Usage:
        return Usage(**{f: getattr(self, f) + getattr(other, f) for f in Usage.model_fields})


class TargetResponse(BaseModel):
    """One answer to one prompt. A target never raises for any outcome; it returns one of these."""

    model_config = ConfigDict(extra="forbid")

    target_id: str
    outcome: Outcome
    items: list[Recommendation] = Field(default_factory=list)  # at most k
    raw_text: str = ""  # the model's first answer, redacted
    model: str = ""  # the model id the provider reported (or the configured one)
    usage: Usage = Field(default_factory=Usage)
    latency_ms: float = 0.0  # wall time of the original call(s); a cache hit keeps it
    finish_reason: str | None = None
    parse_method: ParseMethod = "none"
    prompt_hash: str = ""
    served_from: Literal["live", "cache"] = "live"
    detail: str = ""  # why it isn't `ok`, in a few words; never a key or a stack trace


@runtime_checkable
class Target(Protocol):
    """Anything that answers an exam prompt: an OpenAI-compatible model, or #30's calibration
    targets."""

    id: str

    async def recommend(self, prompt: str, k: int, *, scenario_id: str | None = None) -> TargetResponse:
        """Up to `k` recommendations for `prompt`. Never raises for a bad answer: the outcome says."""


__all__ = ["Outcome", "ParseMethod", "Recommendation", "Target", "TargetResponse", "Usage"]
