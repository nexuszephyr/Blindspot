# 0003: Follow the live API's `urn:entity:videogame`

- Status: Accepted
- Date: 2026-10-16

## Context
The official hackathon developer guide lists `urn:entity:video_game`. During the Phase 1 fix pass,
the live hackathon API returned 200 for `urn:entity:videogame` and 400 for `urn:entity:video_game`,
on both `/v2/insights` and `/search`.

## Decision
The code uses `urn:entity:videogame`, matching the live API. The enum keeps a comment pointing here.

## Consequences
- If Qloo changes the API to match its guide, one enum value changes. A live test checks this.
- We asked on Discord which spelling is correct.

## Alternatives considered
- Follow the guide: every video-game request would fail.
- Accept both: hides the problem and doubles cache keys.
