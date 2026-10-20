"""The exam is data (config/*.yaml), validated here. Never hard-code cities, domains or personas in code.

Files and what they hold (field reference: docs/scenarios.md):

- cities.yaml: where the exam is run.
- domains.yaml: what is asked, and the Qloo query shape for each domain (E12 to E14).
- probe_types.yaml: how a question is varied (core, swap, bridge, audience, freshness).
- personas.yaml: who is asking. Taste only, never identity.

`load_catalog()` loads all four and checks every cross-reference. Errors are `ConfigError`, which
names the file, the item and the field.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .qloo.errors import QlooParamError
from .qloo.models import INSIGHTS_TYPES, SEARCH_TYPES
from .qloo.params import check_params

SignalKind = Literal["audience", "entity", "tag"]
ProbeId = Literal["core", "swap", "bridge", "audience", "freshness"]
# Qloo signal.demographics.age values. 35_and_younger and 55_and_older are VERIFIED (recon E5);
# 36_to_55 is documented but UNVERIFIED until #15 sends it. Age never reaches dining (E5, E11).
AgeBand = Literal["35_and_younger", "36_to_55", "55_and_older"]

# Identity-based audiences (AGENTS §13): opt-in only. Parent types from GET /v2/audiences, plus words
# that would make a persona about who someone is rather than what they like.
IDENTITY_AUDIENCE_TYPES = frozenset({"communities", "political_preferences", "lifestyle_preferences_beliefs"})
IDENTITY_WORDS = re.compile(
    r"religio|christian|muslim|islam|jewish|hindu|buddhis|sikh|atheis|lgbt|gay|lesbian|queer|"
    r"transgender|orientation|ethnic|\brace\b|racial|african|asian|latin[oax]|hispanic|black|white|"
    r"caste|immigra|politic",
    re.I,
)
AUDIENCE_URN = re.compile(r"^urn:audience:([a-z0-9_]+):[\w.\-]+$")


class ConfigError(ValueError):
    """A config file failed validation. The message names the file, the item and the field."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- cities and domains
class City(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    display_name: str
    location: str = Field(min_length=1, max_length=100)  # sent as *.location.query
    country: str = Field(pattern=r"^[A-Z]{2}$")
    script_note: str = ""
    tier: int = Field(ge=1, le=3)


class QueryShape(_Strict):
    """How the answer key for a domain is asked (E13, E14; table in docs/qloo_api_reality.md)."""

    signal: SignalKind  # the persona signal added to the location for the answer key
    candidate_scoring: list[SignalKind] = Field(default_factory=list)  # signals that score an agent's picks


class Domain(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    filter_type: str
    take: int = Field(ge=1, le=50)  # the standard (location-only) query size used by recon scripts
    phrasing: str
    location_param: Literal["filter", "signal"]
    params: dict[str, Any] = Field(default_factory=dict)
    tag_query: str = ""
    depth: int = Field(default=20, ge=1, le=50)  # answer-key depth (E12, D34)
    membership_pool: int = Field(default=50, ge=1, le=50)  # top-N pool for picks outside the key (E14)
    query_shape: QueryShape | None = None
    probes: list[str] = Field(default_factory=list)  # enabled probe types; each must allow this domain

    @field_validator("filter_type")
    @classmethod
    def _official(cls, v: str) -> str:
        if v not in INSIGHTS_TYPES:
            raise ValueError(f"{v!r} is not an insights filter.type; valid: {sorted(INSIGHTS_TYPES)}")
        return v

    @model_validator(mode="after")
    def _pool_covers_depth(self) -> Domain:
        if self.membership_pool < self.depth:
            raise ValueError(f"membership_pool ({self.membership_pool}) is smaller than depth ({self.depth})")
        return self

    def location_params(self, city: City) -> dict[str, str]:
        return {f"{self.location_param}.location.query": city.location}


# --------------------------------------------------------------------------- probe types
class ProbeType(_Strict):
    id: ProbeId
    description: str = Field(min_length=10)
    domains: list[str] = Field(min_length=1)  # domains this probe is allowed in
    scenarios_per_cell: int = Field(default=1, ge=1, le=5)
    max_scenarios: int = Field(ge=0, le=200)  # cap for the generator (#16)
    params: dict[str, Any] = Field(default_factory=dict)  # extra answer-key params, VERIFIED per domain
    pairs: dict[str, str] = Field(default_factory=dict)  # bridge only: asked domain -> stated domain
    condition: dict[str, str] = Field(default_factory=dict)  # domain -> check that must pass first

    @model_validator(mode="after")
    def _pairs_only_for_bridge(self) -> ProbeType:
        if self.id == "bridge" and not self.pairs:
            raise ValueError("bridge needs `pairs` (asked domain -> stated domain)")
        if self.id != "bridge" and self.pairs:
            raise ValueError("`pairs` is only for the bridge probe")
        for asked, stated in self.pairs.items():
            if asked == stated:
                raise ValueError(f"pairs: {asked} -> {stated} bridges a domain to itself")
        return self


# --------------------------------------------------------------------------- personas
class Seed(_Strict):
    name: str = Field(min_length=1, max_length=100)  # resolved to an id by #15; never sent as text
    type: str

    @field_validator("type")
    @classmethod
    def _search_type(cls, v: str) -> str:
        if v not in SEARCH_TYPES:
            raise ValueError(f"{v!r} is not a /search type; valid: {sorted(SEARCH_TYPES)}")
        return v


class AudienceName(_Strict):
    """An audience to be resolved by #15 against GET /v2/audiences (there's no name search)."""

    name: str = Field(min_length=2, max_length=60)
    type: str = Field(pattern=r"^[a-z0-9_]+$")  # the audience parent type, for example `leisure`


AudienceRef = str | AudienceName  # a resolved `urn:audience:<type>:<id>`, or a name to resolve


def audience_type(a: AudienceRef) -> str:
    if isinstance(a, AudienceName):
        return a.type
    m = AUDIENCE_URN.match(a)
    return m[1] if m else ""


def audience_key(a: AudienceRef) -> str:
    return a if isinstance(a, str) else f"{a.type}:{a.name.strip().lower()}"


class Persona(_Strict):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    descriptor: str = Field(min_length=10, max_length=120)  # one neutral line about taste
    age_band: AgeBand
    audiences: list[AudienceRef] = Field(default_factory=list, max_length=3)
    domains: list[str] = Field(min_length=1)  # domains this persona is examined in
    cities: list[str] = Field(default_factory=list)  # empty = every city
    seeds: dict[str, list[Seed]] = Field(default_factory=dict)  # domain -> 1 to 3 seed entities
    interest_tags: dict[str, list[str]] = Field(default_factory=dict)  # domain -> 1 to 3 tag names
    notes: str = ""

    @field_validator("audiences")
    @classmethod
    def _audience_shape(cls, v: list[AudienceRef]) -> list[AudienceRef]:
        for a in v:
            if isinstance(a, str) and not AUDIENCE_URN.match(a):
                raise ValueError(f"{a!r} is not an audience URN (urn:audience:<type>:<id>) or {{name, type}}")
        if len({audience_key(a) for a in v}) != len(v):
            raise ValueError("duplicate audience")
        return v

    @field_validator("seeds")
    @classmethod
    def _seed_counts(cls, v: dict[str, list[Seed]]) -> dict[str, list[Seed]]:
        for d, seeds in v.items():
            if not 1 <= len(seeds) <= 3:
                raise ValueError(f"seeds.{d}: give 1 to 3 seeds, got {len(seeds)}")
        return v

    @field_validator("interest_tags")
    @classmethod
    def _tag_counts(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        for d, tags in v.items():
            if not 1 <= len(tags) <= 3 or not all(t.strip() for t in tags):
                raise ValueError(f"interest_tags.{d}: give 1 to 3 non-empty tag names")
        return v

    @model_validator(mode="after")
    def _own_keys(self) -> Persona:
        for field in ("seeds", "interest_tags"):
            extra = sorted(set(getattr(self, field)) - set(self.domains))
            if extra:
                raise ValueError(f"{field} has domains {extra} that aren't in this persona's `domains`")
        if len(set(self.domains)) != len(self.domains):
            raise ValueError("duplicate domain in `domains`")
        return self


class AudiencePolicy(_Strict):
    allow_identity: bool = False  # identity-based audiences are opt-in and off by default (AGENTS §13)


# --------------------------------------------------------------------------- loading
class Catalog(BaseModel):
    cities: list[City]
    domains: list[Domain]
    probe_types: list[ProbeType]
    personas: list[Persona]
    audience_policy: AudiencePolicy

    def domain(self, domain_id: str) -> Domain:
        return next(d for d in self.domains if d.id == domain_id)

    def probe(self, probe_id: str) -> ProbeType:
        return next(p for p in self.probe_types if p.id == probe_id)

    def persona_cities(self, persona: Persona) -> list[City]:
        return [c for c in self.cities if not persona.cities or c.id in persona.cities]


def _read(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"{path}: file not found")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: not valid YAML: {exc}") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a mapping at the top level")
    return data


def _where(path: Path, key: str, i: int, item: Any) -> str:
    ident = item.get("id") if isinstance(item, dict) else None
    return f"{path}: {key}[{i}]" + (f" (id={ident})" if ident else "")


def _load(path: Path, key: str, model):
    items = _read(Path(path)).get(key) or []
    if not isinstance(items, list):
        raise ConfigError(f"{path}: `{key}` must be a list")
    out = []
    for i, item in enumerate(items):
        try:
            out.append(model.model_validate(item))
        except ValidationError as exc:
            errs = "; ".join(
                f"{'.'.join(map(str, e['loc'])) or '(item)'}: {e['msg'].removeprefix('Value error, ')}"
                for e in exc.errors()
            )
            raise ConfigError(f"{_where(Path(path), key, i, item)}: {errs}") from None
    ids = [x.id for x in out]
    dupes = sorted({x for x in ids if ids.count(x) > 1})
    if dupes:
        raise ConfigError(f"{path}: duplicate ids {dupes}")
    return out


def load_cities(config_dir: Path | str = "config") -> list[City]:
    return _load(Path(config_dir) / "cities.yaml", "cities", City)


def load_domains(config_dir: Path | str = "config") -> list[Domain]:
    return _load(Path(config_dir) / "domains.yaml", "domains", Domain)


def load_probe_types(config_dir: Path | str = "config") -> list[ProbeType]:
    return _load(Path(config_dir) / "probe_types.yaml", "probe_types", ProbeType)


def load_audience_policy(config_dir: Path | str = "config") -> AudiencePolicy:
    path = Path(config_dir) / "personas.yaml"
    try:
        return AudiencePolicy.model_validate(_read(path).get("audience_policy") or {})
    except ValidationError as exc:
        raise ConfigError(f"{path}: audience_policy: {exc.errors()[0]['msg']}") from None


def load_personas(config_dir: Path | str = "config") -> list[Persona]:
    """Personas, checked on their own and against the policy. `load_catalog` adds the cross-checks."""
    path = Path(config_dir) / "personas.yaml"
    personas = _load(path, "personas", Persona)
    if not load_audience_policy(config_dir).allow_identity:
        for p in personas:
            _reject_identity(path, p)
    return personas


def _reject_identity(path: Path, p: Persona) -> None:
    for a in p.audiences:
        label = a if isinstance(a, str) else a.name
        if audience_type(a) in IDENTITY_AUDIENCE_TYPES or IDENTITY_WORDS.search(label):
            raise ConfigError(
                f"{path}: persona {p.id}: audiences: {label!r} is identity-based; set "
                "audience_policy.allow_identity: true to opt in (AGENTS §13)"
            )
    if IDENTITY_WORDS.search(p.descriptor):
        raise ConfigError(f"{path}: persona {p.id}: descriptor: describe taste, not identity")


def load_catalog(config_dir: Path | str = "config") -> Catalog:
    """Load every config file and check that all references resolve."""
    root = Path(config_dir)
    cat = Catalog(
        cities=load_cities(root),
        domains=load_domains(root),
        probe_types=load_probe_types(root),
        personas=load_personas(root),
        audience_policy=load_audience_policy(root),
    )
    _check_probes(root, cat)  # first: domains.yaml refers to these
    _check_domains(root, cat)
    _check_personas(root, cat)
    return cat


def _check_domains(root: Path, cat: Catalog) -> None:
    path = root / "domains.yaml"
    probes = {p.id: p for p in cat.probe_types}
    for d in cat.domains:
        if d.query_shape is None:
            raise ConfigError(f"{path}: domain {d.id}: query_shape is required")
        if not d.probes:
            raise ConfigError(f"{path}: domain {d.id}: probes: enable at least one probe type")
        for pid in d.probes:
            if pid not in probes:
                raise ConfigError(f"{path}: domain {d.id}: probes: unknown probe type {pid!r}")
            if d.id not in probes[pid].domains:
                raise ConfigError(
                    f"{path}: domain {d.id}: probes: {pid!r} isn't allowed for {d.id} in probe_types.yaml"
                )


def _check_probes(root: Path, cat: Catalog) -> None:
    path = root / "probe_types.yaml"
    domains = {d.id: d for d in cat.domains}
    for p in cat.probe_types:
        refs = [*p.domains, *p.condition, *p.pairs, *p.pairs.values()]
        unknown = sorted({x for x in refs if x not in domains})
        if unknown:
            raise ConfigError(f"{path}: probe {p.id}: unknown domain(s) {unknown}")
        outside = sorted((set(p.condition) | set(p.pairs)) - set(p.domains))
        if outside:
            raise ConfigError(f"{path}: probe {p.id}: {outside} must also be listed in `domains`")
        for did in p.domains:  # silently ignored params would corrupt the key: VERIFIED only (ADR 0005)
            try:
                check_params(
                    "/v2/insights", {"filter.type": domains[did].filter_type, **p.params}, strict=True
                )
            except QlooParamError as exc:
                raise ConfigError(f"{path}: probe {p.id}: params for {did}: {exc}") from None


def _check_personas(root: Path, cat: Catalog) -> None:
    path = root / "personas.yaml"
    domains = {d.id: d for d in cat.domains}
    cities = {c.id for c in cat.cities}
    owners: dict[frozenset[str], str] = {}
    for p in cat.personas:
        where = f"{path}: persona {p.id}"
        unknown = sorted(set(p.domains) - set(domains))
        if unknown:
            raise ConfigError(f"{where}: domains: unknown domain(s) {unknown}")
        unknown = sorted(set(p.cities) - cities)
        if unknown:
            raise ConfigError(f"{where}: cities: unknown city id(s) {unknown}")
        for did in p.domains:
            signal = domains[did].query_shape.signal
            if signal == "audience" and not p.audiences:
                raise ConfigError(
                    f"{where}: audiences: required, because {did}'s query shape uses an audience signal"
                    " (age and gender don't change places, E5/E13)"
                )
            if signal == "entity" and not p.seeds.get(did):
                raise ConfigError(f"{where}: seeds.{did}: required by {did}'s entity query shape")
            if signal == "tag" and not p.interest_tags.get(did):
                raise ConfigError(f"{where}: interest_tags.{did}: required by {did}'s tag query shape")
            if not (p.seeds.get(did) or p.interest_tags.get(did)):
                raise ConfigError(
                    f"{where}: {did}: state a taste in seeds.{did} or interest_tags.{did} (prompts use it)"
                )
        # With an audience-only shape, two personas with the same audiences get the same answer key.
        key = frozenset(audience_key(a) for a in p.audiences)
        if key and key in owners:
            raise ConfigError(
                f"{where}: audiences: same set as persona {owners[key]}; their answer keys would match"
            )
        owners[key] = p.id
