"""F5 and #14: the exam is data (cities, domains, probe types, personas), validated, and never
hard-coded in src/."""

import shutil
from pathlib import Path

import pytest
import yaml

from blindspot.catalog import (
    IDENTITY_AUDIENCE_TYPES,
    IDENTITY_WORDS,
    ConfigError,
    audience_type,
    load_catalog,
    load_cities,
    load_domains,
    load_personas,
)

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).parent / "fixtures" / "config"


def test_test_config_loads():
    cities, domains = load_cities(FIX), load_domains(FIX)
    assert [c.id for c in cities] == ["example_city", "other_town"]
    assert domains[0].location_params(cities[0]) == {"filter.location.query": "Example City"}


def test_real_config_is_valid():
    assert len(load_cities(ROOT / "config")) >= 1 and len(load_domains(ROOT / "config")) >= 1


def test_bad_config_rejected(tmp_path):
    (tmp_path / "domains.yaml").write_text(
        yaml.safe_dump(
            {
                "domains": [
                    {
                        "id": "games",
                        "filter_type": "urn:entity:video_game",
                        "take": 5,
                        "phrasing": "x",
                        "location_param": "signal",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="videogame"):
        load_domains(tmp_path)


def test_no_configured_city_in_src():
    names = set()
    for c in load_cities(ROOT / "config"):
        names |= {c.display_name, c.location}
    hits = [
        f"{p.relative_to(ROOT)}: {n}"
        for p in (ROOT / "src").rglob("*.py")
        for n in names
        if n in p.read_text(encoding="utf-8")
    ]
    assert not hits, f"cities must live in config/, found in code: {hits}"


# --- personas, probe types and domain query shapes (#14) -------------------------------------------


def _copy(tmp_path: Path) -> Path:
    for f in FIX.glob("*.yaml"):
        shutil.copy(f, tmp_path / f.name)
    return tmp_path


def _edit(root: Path, name: str, fn) -> None:
    path = root / f"{name}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    fn(data)
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def _rejects(tmp_path, name, fn, *needles):
    root = _copy(tmp_path)
    _edit(root, name, fn)
    with pytest.raises(ConfigError) as exc:
        load_catalog(root)
    msg = str(exc.value)
    for n in (f"{name}.yaml", *needles):
        assert n in msg, f"{n!r} missing from: {msg}"
    return msg


def test_fixture_catalog_loads():
    cat = load_catalog(FIX)
    assert [p.id for p in cat.personas] == ["test_snacker", "test_listener"]
    assert [p.id for p in cat.probe_types] == ["core", "bridge", "freshness"]
    assert cat.domain("dining").query_shape.signal == "audience"
    assert [c.id for c in cat.persona_cities(cat.personas[1])] == ["other_town"]
    assert len(cat.persona_cities(cat.personas[0])) == 2


def test_real_catalog_is_valid():
    cat = load_catalog(ROOT / "config")
    assert 12 <= len(load_personas(ROOT / "config")) <= 16
    assert {p.id for p in cat.probe_types} == {"core", "swap", "bridge", "audience", "freshness"}
    for d in cat.domains:
        assert d.query_shape and d.probes and d.depth <= d.membership_pool
    assert not cat.audience_policy.allow_identity
    assert 80 <= sum(p.max_scenarios for p in cat.probe_types) <= 120  # #16's target range
    ages = {p.age_band for p in cat.personas}
    assert ages == {"35_and_younger", "36_to_55", "55_and_older"}


def test_bad_value_names_file_item_and_field(tmp_path):
    msg = _rejects(tmp_path, "personas", lambda d: d["personas"][0].update(age_band="18_to_99"), "age_band")
    assert "personas[0] (id=test_snacker)" in msg


def test_dining_persona_without_audience_rejected(tmp_path):
    _rejects(tmp_path, "personas", lambda d: d["personas"][0].update(audiences=[]), "dining", "audiences")


@pytest.mark.parametrize(
    "audience",
    [
        "urn:audience:communities:test_group",
        "urn:audience:political_preferences:test_view",
        {"name": "religious holidays", "type": "leisure"},
        {"name": "test group", "type": "lifestyle_preferences_beliefs"},
    ],
)
def test_identity_audiences_rejected_unless_allowed(tmp_path, audience):
    msg = _rejects(tmp_path, "personas", lambda d: d["personas"][0].update(audiences=[audience]), "identity")
    assert "allow_identity" in msg
    _edit(tmp_path, "personas", lambda d: d["audience_policy"].update(allow_identity=True))
    assert load_catalog(tmp_path).personas[0].audiences  # opt-in works


def test_identity_descriptor_rejected(tmp_path):
    _rejects(
        tmp_path,
        "personas",
        lambda d: d["personas"][0].update(descriptor="A religious person who likes snacks"),
        "descriptor",
    )


@pytest.mark.parametrize(
    "name, fn, needles",
    [
        ("personas", lambda d: d["personas"][1].update(domains=["music", "poetry"]), ["poetry"]),
        ("personas", lambda d: d["personas"][1].update(cities=["atlantis"]), ["atlantis", "cities"]),
        ("domains", lambda d: d["domains"][0].update(probes=["core", "rumour"]), ["rumour"]),
        ("domains", lambda d: d["domains"][1].update(probes=["core", "freshness"]), ["freshness", "music"]),
        ("probe_types", lambda d: d["probe_types"][0].update(domains=["dining", "poetry"]), ["poetry"]),
        ("probe_types", lambda d: d["probe_types"][1].update(pairs={"music": "poetry"}), ["poetry"]),
        (
            "probe_types",
            lambda d: d["probe_types"].append(dict(d["probe_types"][0], id="rumour")),
            ["rumour"],
        ),
    ],
)
def test_unknown_references_rejected(tmp_path, name, fn, needles):
    _rejects(tmp_path, name, fn, *needles)


def test_probe_params_must_be_verified_for_every_allowed_domain(tmp_path):
    # bias.trends is INEFFECTIVE for artists (do-not-retry list): silently ignored, so never in a key
    _rejects(
        tmp_path,
        "probe_types",
        lambda d: d["probe_types"][2].update(domains=["dining", "music"]),
        "bias.trends",
    )


def test_shared_audience_set_rejected(tmp_path):
    same = ["urn:audience:hobbies_and_interests:test_snacks"]
    _rejects(tmp_path, "personas", lambda d: d["personas"][1].update(audiences=same), "test_snacker")


@pytest.mark.parametrize(
    "name, fn, needles",
    [
        (
            "personas",
            lambda d: d["personas"][0]["seeds"]["music"].extend(
                [{"name": "x", "type": "urn:entity:artist"}] * 3
            ),
            ["seeds.music"],
        ),
        (
            "personas",
            lambda d: d["personas"][0]["seeds"]["music"][0].update(type="urn:entity:video_game"),
            ["type"],
        ),
        (
            "personas",
            lambda d: d["personas"][1]["seeds"].update(dining=[{"name": "x", "type": "urn:entity:brand"}]),
            ["dining"],
        ),
        ("personas", lambda d: d["personas"][1].update(seeds={}), ["music", "seeds"]),
        ("personas", lambda d: d["personas"][0].update(nickname="x"), ["nickname"]),
        ("domains", lambda d: d["domains"][0].update(depth=20, membership_pool=10), ["membership_pool"]),
        ("domains", lambda d: d["domains"][0].pop("query_shape"), ["query_shape"]),
        ("domains", lambda d: d["domains"][0]["query_shape"].update(signal="vibes"), ["signal"]),
        ("probe_types", lambda d: d["probe_types"][1].pop("pairs"), ["pairs"]),
    ],
)
def test_field_rules(tmp_path, name, fn, needles):
    _rejects(tmp_path, name, fn, *needles)


def test_real_personas_describe_taste_not_identity():
    cat = load_catalog(ROOT / "config")
    for p in cat.personas:
        assert not IDENTITY_WORDS.search(p.descriptor), p.id
        for a in p.audiences:
            assert audience_type(a) not in IDENTITY_AUDIENCE_TYPES, p.id
