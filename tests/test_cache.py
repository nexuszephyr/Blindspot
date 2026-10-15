import httpx

from blindspot.qloo.cache import ResponseCache, cache_key, normalize_params
from blindspot.qloo.db import Database


def test_key_stable_under_param_reordering():
    a = cache_key(
        "GET", "/v2/insights", {"filter.type": "urn:entity:artist", "take": 5, "bias.trends": "high"}
    )
    b = cache_key(
        "get", "v2/insights/", {"bias.trends": "high", "take": "5", "filter.type": "urn:entity:artist"}
    )
    assert a == b


def test_equivalent_forms_share_key():
    assert cache_key("GET", "/search", {"types": ["a", "b"], "q": " x "}) == cache_key(
        "GET", "/search", {"q": "x", "types": "a, b", "unused": None, "empty": ""}
    )
    assert normalize_params({"flag": True, "n": 5.0}) == {"flag": "true", "n": "5"}


def test_different_queries_differ():
    assert cache_key("GET", "/search", {"query": "a"}) != cache_key("GET", "/search", {"query": "b"})
    # list order is preserved (it may be meaningful to Qloo)
    assert cache_key("GET", "/search", {"types": ["a", "b"]}) != cache_key(
        "GET", "/search", {"types": ["b", "a"]}
    )


def test_hit_miss_and_ttl(tmp_path):
    cache = ResponseCache(Database(tmp_path / "c.sqlite"), ttl_days=1)
    k = cache_key("GET", "/search", {"query": "x"})
    assert cache.get(k) is None
    cache.put(k, "GET", "/search", {"query": "x"}, 200, {"results": []}, fetched_at=1000.0)
    assert cache.get(k, now=1000.0 + 3600).body == {"results": []}
    assert cache.get(k, now=1000.0 + 2 * 86400) is None  # expired
    cache.put(k, "GET", "/search", {"query": "x"}, 200, {"results": []}, fetched_at=1000.0, source="snapshot")
    assert cache.get(k, now=1000.0 + 365 * 86400) is not None  # snapshot rows don't expire


async def test_client_cache_hit_avoids_network(make_client, ctx, mock_api, search_body):
    route = mock_api.get("/search").mock(return_value=httpx.Response(200, json=search_body))
    c = make_client()
    r1 = await c.search_entities("x", ctx)
    r2 = await c.search_entities("x", ctx)
    assert (r1.served_from, r2.served_from) == ("live", "cache")
    assert route.call_count == 1
    assert r2.entities[0].name == r1.entities[0].name
    assert (await c.request("/search", {"take": 5, "query": "x"}, ctx)).served_from == "cache"
