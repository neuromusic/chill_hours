# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running

Uses a conda environment. Always run with:
```
conda run -n chill_hours python chill_hours.py
```
Or activate first: `conda activate chill_hours`

Install dependencies: `/home/justin/miniforge3/envs/chill_hours/bin/pip install -r requirements.txt`

## Environment

Requires `.env` with `AMBIENT_API_KEY`, `AMBIENT_APPLICATION_KEY`, and `AMBIENT_ENDPOINT`. Keys come from https://ambientweather.net/account.

In CI, these are stored as GitHub Actions environment secrets in the `github-pages` environment.

## Architecture

Single-file script (`chill_hours.py`) with five sequential stages run by `main()`:

1. **`fetch_weather_data()`** — Calls Ambient Weather REST API directly (not the `ambient_api` library, which is broken). Paginates backwards in 288-record batches (≈1 day of 5-min readings). Caches to `data/raw_weather.parquet`; subsequent runs are incremental, only fetching data newer than the cache. Sleeps 2s between requests for rate limiting.

2. **`compute_chill_hours()`** — Converts UTC timestamps to America/Phoenix local time. Assigns readings to "night dates" (noon-to-noon windows). Counts readings where `tempf` is between 32–45°F, multiplied by 5/60 to get hours.

3. **`print_summary()`** — Prints totals, variety probability table, and last 14 nights.

4. **`plot_chill_hours()`** — Two-panel matplotlib chart (nightly bars + cumulative line with historical overlay) saved to `data/chill_hours.png`.

5. **`write_summary_json()`** — Writes `data/summary.json` with structured stats for the web page (total hours, nights tracked, variety probabilities).

## Project Structure

- `chill_hours.py` — Main script
- `reference_data/daily_chill_hours_phoenix.csv` — Historical chill hours (1975–2024), tracked in git
- `site/index.html` — GitHub Pages web page (loads chart image + summary.json)
- `.github/workflows/update-chill-hours.yml` — Daily CI/CD workflow
- `data/` — Gitignored runtime outputs (parquet cache, chart, summary JSON)

## Deployment

GitHub Actions runs daily at 10:00 UTC (3:00 AM MST). The workflow:
1. Restores the parquet cache from previous runs
2. Runs `chill_hours.py` (incremental fetch)
3. Assembles `_site/` from `site/index.html` + generated chart + summary JSON
4. Deploys to GitHub Pages via `actions/deploy-pages`

Live site: https://neuromusic.github.io/chill_hours/

## Key Details

- API calls use `_api_get()` helper which authenticates via env vars — do not use the `ambient_api` pip package
- `dateutc` field is milliseconds since epoch (not seconds)
- First run fetches ~138 batches (~7 min); re-runs are incremental (seconds)
- `data/` directory is gitignored and auto-created
- `_site/` is gitignored (assembled during CI only)
- Historical CSV lives in `reference_data/` (not `data/`) so it's tracked in git
