# DoPolitics

People say "I don't do politics." Politics does all of us.

DoPolitics takes a street address and returns every federal official who represents it: two U.S. senators and one U.S. representative. For each official it shows recent votes, summarized in plain language, and every way to contact them. There's no signup, no account and no donation ask.

**Status:** early development. The foundation and design decisions are in place; no application code yet.

**Stance:** This is a project to illustrate how politics affects all of us, and that conservative representation often creates or exacerbates problems rather than solving them. It may be labeled a progressive project, but the reality is that the GOP has controlled all three branches of government from January 20, 2025 until the 2026 midterms, and no one's life has gotten better. Its data comes from official government sources, and every summary links to the record it summarizes.

## How it works

```
[Government sources] -> [Scheduled ingestion jobs] -> [Postgres + PostGIS] -> [Cache] -> [Read-only API] -> [Web app]
                                                                                 ^
                                              [Census Geocoder] -----------------+  (per request)
```

1. **Geo-resolution.** The address is geocoded to a point with the U.S. Census Geocoder. A point-in-polygon query against congressional district boundaries, loaded from Census TIGER/Line into PostGIS, then finds the district. Boundaries are stored in-house, so no third-party service decides who represents an address.
2. **Ingestion, not proxying.** Officials, contact info and votes come from government sources on a schedule. Each source has an adapter that fetches, normalizes and upserts the data, keyed on stable IDs so re-runs are safe, and every run is logged. User requests are answered from our own database. The geocoder is the only live call.
3. **Districts change.** Districts are modeled with effective dates, so a mid-decade redistricting doesn't quietly serve the wrong representative.

## Planned v1 scope

- Federal only: U.S. Senate and U.S. House
- Contact channels: DC and district office phones, contact forms, mailing addresses, website, social
- Recent roll-call votes with plain-language summaries
- State legislators and governors come after federal works end to end

## Privacy

Addresses are never stored. An address is geocoded, resolved to districts and discarded. It isn't cached, and it isn't logged.

## Summaries

Vote and bill summaries are generated. Each one is labeled as generated, links to the official source, and follows a published editorial policy. The policy will be added before summaries ship.

## Stack (planned)

| Layer | Choice |
|---|---|
| API | Python, FastAPI |
| Database | PostgreSQL + PostGIS |
| Cache | Redis |
| Local dev | Docker Compose |
| Testing | pytest, Playwright end-to-end |
| CI | GitHub Actions |
| Hosting | Render |

## Data sources

| Source | Used for |
|---|---|
| [Census Geocoder](https://geocoding.geo.census.gov/) | Address to coordinates |
| [Census TIGER/Line](https://www.census.gov/geographies/mapping-files/time-series/geo/tiger-line-file.html) | District boundaries |
| [unitedstates/congress-legislators](https://github.com/unitedstates/congress-legislators) | Current members, IDs, office contacts |
| [Congress.gov API](https://api.congress.gov/) | Bills, actions, official summaries |
| [House Clerk](https://clerk.house.gov/) / [Senate.gov](https://www.senate.gov/) | Roll-call votes |
