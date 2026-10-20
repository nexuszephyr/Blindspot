"""The exam is data (config/*.yaml), validated here. Never hard-code cities, domains, personas or
probe types in code.

Files and what they hold (field reference: docs/scenarios.md):

- cities.yaml: where the exam is run.
- domains.yaml: what is asked, and the Qloo query shape for each domain (E12 to E14).
- probe_types.yaml: how a question is varied (core, swap, bridge, audience, freshness).
- personas.yaml: who is asking. Taste only, never identity.

`load_catalog()` loads all four and checks every cross-reference. Anything that would load silently
but mean something else (an unknown key, an unknown reference, a typo in a condition) is an error,
for the same reason Qloo's silent-ignore is guarded on the API side. Errors are `ConfigError`, which
names the file, the item and the field.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .qloo.errors import QlooParamError
from .qloo.models import INSIGHTS_TYPES, SEARCH_TYPES
from .qloo.params import check_params

SignalKind = Literal["audience", "entity", "tag"]

# Qloo signal.demographics.age values. 36_to_55 is documented but UNVERIFIED until #15 sends it.
# No current query shape sends age (every domain uses the audience, E13); a shape that sets
# `with_age: true` only loads if every persona in that domain has a VERIFIED band.
AgeBand = Literal["35_and_younger", "36_to_55", "55_and_older"]
VERIFIED_AGE_BANDS = frozenset({"35_and_younger", "55_and_older"})  # recon E5

# Checks the scenario generator (#16) implements. A `condition` naming anything else fails to load,
# so a typo can't silently drop a guard such as D37's dining check.
KNOWN_CONDITIONS = frozenset({"personas_distinct_in_city"})

# Identity-based audiences (AGENTS §13) are opt-in. Matching is on whole tokens (split on anything
# that isn't a letter), so taste words such as "white-tablecloth", "black coffee" or "Southeast
# Asian street food" pass, while `race_ethnicity` inside a URN is caught (by "ethnicity"; bare
# "race" is left out because it is also a sport: "race-day food").
IDENTITY_AUDIENCE_TYPES = frozenset({"communities", "political_preferences", "lifestyle_preferences_beliefs"})
IDENTITY_TOKENS = frozenset(
    {
        "religion", "religions", "religious", "faith", "christian", "christians", "muslim", "muslims",
        "islam", "islamic", "jewish", "judaism", "hindu", "hindus", "buddhist", "buddhists", "sikh",
        "sikhs", "atheist", "atheists", "ethnicity", "ethnicities", "ethnic", "racial",
        "lgbt", "lgbtq", "gay", "lesbian", "bisexual", "queer", "transgender", "orientation", "caste",
        "castes", "immigrant", "immigrants", "immigration", "political", "politics", "conservative",
        "liberal", "nationality",
    }
)  # fmt: skip
AUDIENCE_URN = re.compile(r"^urn:audience:([a-z0-9_]+):([\w.\-]+)$")


def identity_terms(text: str) -> list[str]:
    """Identity words in `text`, as whole tokens. Empty when the text is about taste."""
    return sorted({t for t in re.findall(r"[a-z]+", text.lower()) if t in IDENTITY_TOKENS})


class ConfigError(ValueError):
    """A config file failed validation. The message names the file, the item and the field."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")  # an unknown or misspelled key fails instead of defaulting


# --------------------------------------------------------------------------- cities and domains
class City(_Strict):
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
    with_age: bool = False  # also send the persona's age band (never for places: no effect, E5)


class Domain(_Strict):
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
        if self.query_shape and self.query_shape.with_age and self.filter_type == "urn:entity:place":
            raise ValueError("query_shape.with_age: age doesn't change places (E5); use the audience")
        return self

    def location_params(self, city: City) -> dict[str, str]:
        return {f"{self.location_param}.location.query": city.location}


# --------------------------------------------------------------------------- probe types
class ProbeType(_Strict):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    description: str = Field(min_length=10)
    domains: list[str] = Field(min_length=1)  # domains this probe is allowed in
    scenarios_per_cell: int = Field(default=1, ge=1, le=5)
    max_scenarios: int = Field(ge=0, le=200)  # cap for the generator (#16)
    params: dict[str, Any] = Field(default_factory=dict)  # extra answer-key params, VERIFIED per domain
    requires_pairs: bool = False  # the taste is stated in another domain (bridge)
    pairs: dict[str, str] = Field(default_factory=dict)  # asked domain -> stated domain
    condition: dict[str, str] = Field(default_factory=dict)  # domain -> a KNOWN_CONDITIONS check

    @model_validator(mode="after")
    def _pairs_and_conditions(self) -> ProbeType:
        if self.requires_pairs and not self.pairs:
            raise ValueError("requires_pairs is true but `pairs` is empty")
        if self.pairs and not self.requires_pairs:
            raise ValueError("`pairs` is set but requires_pairs is false")
        for asked, stated in self.pairs.items():
            if asked == stated:
                raise ValueError(f"pairs: {asked} -> {stated} bridges a domain to itself")
        for domain, check in self.condition.items():
            if check not in KNOWN_CONDITIONS:
                raise ValueError(
                    f"condition.{domain}: unknown check {check!r}; known: {sorted(KNOWN_CONDITIONS)}"
                )
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


def audience_key(a: AudienceRef, resolved: Mapping[str, str] | None = None) -> str:
    """One comparable key per audience, whichever form it's written in. Before #15 resolves names,
    `{name: fine dining, type: leisure}` and `urn:audience:leisure:fine_dining` share a key; after,
    pass #15's {name key: URN} map so both forms compare as the resolved URN."""
    if isinstance(a, str):
        m = AUDIENCE_URN.match(a)
        return f"{m[1]}:{m[2].lower()}" if m else a
    name_key = f"{a.type}:{re.sub(r'[^a-z0-9]+', '_', a.name.lower()).strip('_')}"
    if resolved and name_key in resolved:
        return audience_key(resolved[name_key])
    return name_key


class Persona(_Strict):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    descriptor: str = Field(min_length=10, max_length=120)  # one neutral line about taste
    age_band: AgeBand
    audiences: list[AudienceRef] = Field(default_factory=list, max_length=3)
    domains: list[str] = Field(min_length=1)  # domains this persona is examined in
    cities: list[str] = Field(default_factory=list)  # empty = every city
    seeds: dict[str, list[Seed]] = Field(default_factory=dict)  # domain -> 1 to 3 seed entities
    interest_tags: dict[str, list[str]] = Field(default_factory=dict)  # domain -> 1 to 3 tag names
    exclude_probes: list[str] = Field(default_factory=list)  # probe types never generated for it
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

    def prompt_texts(self) -> dict[str, str]:
        """Every text from this persona that can reach a target prompt or a Qloo lookup."""
        out = {"descriptor": self.descriptor, "notes": self.notes}
        for a in self.audiences:
            out[f"audiences ({a if isinstance(a, str) else a.name})"] = a if isinstance(a, str) else a.name
        for d, tags in self.interest_tags.items():
            out |= {f"interest_tags.{d}": t for t in tags}
        for d, seeds in self.seeds.items():
            out |= {f"seeds.{d}": s.name for s in seeds}
        return out


class AudiencePolicy(_Strict):
    allow_identity: bool = False  # identity-based audiences are opt-in and off by default (AGENTS §13)


# --------------------------------------------------------------------------- the catalog
class Catalog(BaseModel):
    cities: list[City]
    domains: list[Domain]
    probe_types: list[ProbeType]
    personas: list[Persona]
    audience_policy: AudiencePolicy

    def domain(self, domain_id: str) -> Domain:
        return _by_id(self.domains, domain_id, "domain")

    def probe(self, probe_id: str) -> ProbeType:
        return _by_id(self.probe_types, probe_id, "probe type")

    def persona(self, persona_id: str) -> Persona:
        return _by_id(self.personas, persona_id, "persona")

    def city(self, city_id: str) -> City:
        return _by_id(self.cities, city_id, "city")

    def persona_cities(self, persona: Persona) -> list[City]:
        return [c for c in self.cities if not persona.cities or c.id in persona.cities]

    def persona_probes(self, persona: Persona, domain_id: str) -> list[str]:
        """Probe types to generate for this persona in this domain."""
        return [p for p in self.domain(domain_id).probes if p not in persona.exclude_probes]


def _by_id(items: list, item_id: str, kind: str):
    found = next((x for x in items if x.id == item_id), None)
    if found is None:
        raise KeyError(f"unknown {kind} id {item_id!r}; known: {[x.id for x in items]}")
    return found


# --------------------------------------------------------------------------- loading
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


def _errors(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(map(str, e['loc'])) or '(item)'}: {e['msg'].removeprefix('Value error, ')}"
        for e in exc.errors()
    )


def _items(path: Path, data: dict[str, Any], key: str, model):
    items = data.get(key) or []
    if not isinstance(items, list):
        raise ConfigError(f"{path}: `{key}` must be a list")
    out = []
    for i, item in enumerate(items):
        try:
            out.append(model.model_validate(item))
        except ValidationError as exc:
            ident = item.get("id") if isinstance(item, dict) else None
            where = f"{path}: {key}[{i}]" + (f" (id={ident})" if ident else "")
            raise ConfigError(f"{where}: {_errors(exc)}") from None
    ids = [x.id for x in out]
    dupes = sorted({x for x in ids if ids.count(x) > 1})
    if dupes:
        raise ConfigError(f"{path}: duplicate ids {dupes}")
    return out


def _load(path: Path, key: str, model):
    return _items(path, _read(path), key, model)


def load_cities(config_dir: Path | str = "config") -> list[City]:
    return _load(Path(config_dir) / "cities.yaml", "cities", City)


def load_domains(config_dir: Path | str = "config") -> list[Domain]:
    return _load(Path(config_dir) / "domains.yaml", "domains", Domain)


def load_probe_types(config_dir: Path | str = "config") -> list[ProbeType]:
    return _load(Path(config_dir) / "probe_types.yaml", "probe_types", ProbeType)


def _personas_and_policy(config_dir: Path | str) -> tuple[list[Persona], AudiencePolicy]:
    """Parse personas.yaml once: the policy used to validate is the policy returned."""
    path = Path(config_dir) / "personas.yaml"
    data = _read(path)
    try:
        policy = AudiencePolicy.model_validate(data.get("audience_policy") or {})
    except ValidationError as exc:
        raise ConfigError(f"{path}: audience_policy: {_errors(exc)}") from None
    personas = _items(path, data, "personas", Persona)
    if not policy.allow_identity:
        for p in personas:
            _reject_identity(path, p)
    return personas, policy


def load_audience_policy(config_dir: Path | str = "config") -> AudiencePolicy:
    return _personas_and_policy(config_dir)[1]


def load_personas(config_dir: Path | str = "config") -> list[Persona]:
    """Personas, checked on their own and against the policy. `load_catalog` adds the cross-checks."""
    return _personas_and_policy(config_dir)[0]


def _reject_identity(path: Path, p: Persona) -> None:
    for a in p.audiences:
        if audience_type(a) in IDENTITY_AUDIENCE_TYPES:
            raise ConfigError(
                f"{path}: persona {p.id}: audiences: type {audience_type(a)!r} is identity-based; set "
                "audience_policy.allow_identity: true to opt in (AGENTS §13)"
            )
    for field, text in p.prompt_texts().items():
        if terms := identity_terms(text):
            raise ConfigError(
                f"{path}: persona {p.id}: {field}: {terms} describe identity, not taste; set "
                "audience_policy.allow_identity: true to opt in (AGENTS §13)"
            )


def check_distinct_audiences(
    personas: list[Persona], resolved: Mapping[str, str] | None = None, *, where: str = "personas.yaml"
) -> None:
    """Every persona needs an audience set no other persona has: the answer key uses the audience
    (D35), so equal sets mean equal keys (D38). load_catalog runs this on the names as written;
    #15 must run it again with its {name key: URN} map, once names have resolved."""
    owners: dict[frozenset[str], str] = {}
    for p in personas:
        key = frozenset(audience_key(a, resolved) for a in p.audiences)
        if key and key in owners:
            raise ConfigError(
                f"{where}: persona {p.id}: audiences: same set as persona {owners[key]}; "
                "their answer keys would be identical"
            )
        owners[key] = p.id


def load_catalog(config_dir: Path | str = "config") -> Catalog:
    """Load every config file and check that all references resolve."""
    root = Path(config_dir)
    personas, policy = _personas_and_policy(root)
    cat = Catalog(
        cities=load_cities(root),
        domains=load_domains(root),
        probe_types=load_probe_types(root),
        personas=personas,
        audience_policy=policy,
    )
    _check_probes(root, cat)  # first: domains.yaml refers to these
    _check_domains(root, cat)
    _check_personas(root, cat)
    return cat


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
            if probes[pid].requires_pairs and d.id not in probes[pid].pairs:
                raise ConfigError(
                    f"{path}: domain {d.id}: probes: {pid!r} needs a pair for {d.id} "
                    f"(probe_types.yaml {pid}.pairs) naming the domain the taste is stated in"
                )


def _check_personas(root: Path, cat: Catalog) -> None:
    path = root / "personas.yaml"
    domains = {d.id: d for d in cat.domains}
    probes = {p.id: p for p in cat.probe_types}
    cities = {c.id for c in cat.cities}
    for p in cat.personas:
        where = f"{path}: persona {p.id}"
        unknown = sorted(set(p.domains) - set(domains))
        if unknown:
            raise ConfigError(f"{where}: domains: unknown domain(s) {unknown}")
        unknown = sorted(set(p.cities) - cities)
        if unknown:
            raise ConfigError(f"{where}: cities: unknown city id(s) {unknown}")
        unknown = sorted(set(p.exclude_probes) - set(probes))
        if unknown:
            raise ConfigError(f"{where}: exclude_probes: unknown probe type(s) {unknown}")
        for did in p.domains:
            _check_persona_domain(where, p, domains[did], probes)
    check_distinct_audiences(cat.personas, where=str(path))


def _check_persona_domain(where: str, p: Persona, d: Domain, probes: dict[str, ProbeType]) -> None:
    shape = d.query_shape
    if shape.signal == "audience" and not p.audiences:
        raise ConfigError(
            f"{where}: audiences: required, because {d.id}'s query shape uses an audience signal"
            " (age and gender don't change places, E5/E13)"
        )
    if shape.signal == "entity" and not p.seeds.get(d.id):
        raise ConfigError(f"{where}: seeds.{d.id}: required by {d.id}'s entity query shape")
    if shape.signal == "tag" and not p.interest_tags.get(d.id):
        raise ConfigError(f"{where}: interest_tags.{d.id}: required by {d.id}'s tag query shape")
    if shape.with_age and p.age_band not in VERIFIED_AGE_BANDS:
        raise ConfigError(
            f"{where}: age_band: {p.age_band!r} is UNVERIFIED, and {d.id}'s query shape sends age; "
            "verify it first (#15) or use a VERIFIED band"
        )
    if not (p.seeds.get(d.id) or p.interest_tags.get(d.id)):
        raise ConfigError(
            f"{where}: {d.id}: state a taste in seeds.{d.id} or interest_tags.{d.id} (prompts use it)"
        )
    for pid in d.probes:
        stated = probes[pid].pairs.get(d.id)
        if stated and pid not in p.exclude_probes and stated not in p.domains:
            raise ConfigError(
                f"{where}: {pid} in {d.id} states the taste in {stated}, which this persona doesn't have; "
                f"add {stated} to its domains or list {pid!r} in exclude_probes"
            )
