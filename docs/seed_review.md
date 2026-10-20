# Seed, tag and audience review

`scripts/resolve_seeds.py` (#15) looks up every persona seed, interest tag and audience in
`config/personas.yaml` once and locks the chosen Qloo id in `config/resolved_ids.lock.json`. This page
lists, by **our own query text**, everything a person should look at. It never shows Qloo names.

- Candidates (the top 3 Qloo returned for each item, with their names) are in
  `data/resolved_ids.detail.json`, which is gitignored. Open it locally to review an item.
- To fix a miss, change the text in `config/personas.yaml` and re-run the script. Unchanged items are
  cache hits; only the new text is looked up.
- Ambiguous items are locked and usable, but should be checked. Unresolved items have no id: a
  persona whose **audience** is unresolved has no answer key (every domain's key uses the audience,
  D35).

## Rules

Names are compared after normalizing: NFKC, casefold, `&` read as "and", punctuation and extra spaces
removed, and a leading "the " dropped (accents are kept). The code is `src/blindspot/idlock.py`.

**Seeds** (`/search` with the seed's own type, top 5):

| Confidence | When |
|---|---|
| 1.0 | The top hit has our name and the requested type |
| 0.7 | The top hit's name starts with or contains ours (or the reverse), and the type matches |
| 0.3 | Anything else (including a top hit of another type) |

Ambiguous when confidence is below 1.0, when two or more of the top 5 are exact name and type
matches (the most popular is taken), or when the top hit's type doesn't match. No hits: unresolved.

**Interest tags** (`/v2/tags`, top 10). Only tags in the domain's families count:

| Domain | Tag families |
|---|---|
| dining | `genre:place`, `category:place`, `cuisine:qloo`, `dining_option:qloo`, `amenity:place` |
| music | `genre:music` |
| film_tv | `genre:media` |
| brands | `category:brand`, `genre:brand`, `keyword:qloo` |

Among those, an exact name match is 1.0 and a contains match 0.7. Ambiguous when more than one
allowed tag matches exactly, or the best is only 0.7 (ties: the most popular). No allowed match:
unresolved, and the reason lists the families that did come back.

**Audiences** (`GET /v2/audiences` per parent type, take 50; there's no audience name search):

- A URN in the config is resolved when it appears in its type's listing, otherwise "not in listing".
- A `{name, type}` entry is matched against the names in that type's listing: exact 1.0, contains
  0.7, ambiguous when several match at the best level. The id taken is the item's `id` URN, never its
  `entity_id` UUID, which Qloo doesn't accept as an audience (E5).
- A resolved audience whose type or id is identity-based is forced to unresolved (AGENTS §13).
- Finally the distinct-audience check (D38) runs again on the resolved URNs. If two personas end up
  with the same set, the script stops without writing the lock.

## Results

Not run yet: the live run is waiting on the local live-call cap (see the PR).
