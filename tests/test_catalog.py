"""F5: cities/domains are data, validated, and never hard-coded in src/."""

from pathlib import Path

import pytest
import yaml

from blindspot.catalog import load_cities, load_domains

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
                        "filter_type": "urn:entity:videogame",
                        "take": 5,
                        "phrasing": "x",
                        "location_param": "signal",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="video_game"):
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
