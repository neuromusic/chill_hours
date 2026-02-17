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

## Architecture

Single-file script (`chill_hours.py`) with four sequential stages run by `main()`:

1. **`fetch_weather_data()`** — Calls Ambient Weather REST API directly (not the `ambient_api` library, which is broken). Paginates backwards in 288-record batches (≈1 day of 5-min readings). Caches to `data/raw_weather.parquet`; subsequent runs are incremental, only fetching data newer than the cache. Sleeps 2s between requests for rate limiting.

2. **`compute_chill_hours()`** — Converts UTC timestamps to America/Phoenix local time. Assigns readings to "night dates" (6 PM–8 AM; early-morning hours belong to the previous calendar date's night). Counts readings where `tempf` is between 32–45°F, multiplied by 5/60 to get hours.

3. **`print_summary()`** — Prints totals and last 14 nights table.

4. **`plot_chill_hours()`** — Two-panel matplotlib chart (nightly bars + cumulative line) saved to `data/chill_hours.png`.

## Key Details

- API calls use `_api_get()` helper which authenticates via env vars — do not use the `ambient_api` pip package
- `dateutc` field is milliseconds since epoch (not seconds)
- First run fetches ~138 batches (~5 min); re-runs are incremental (seconds)
- `data/` directory is gitignored and auto-created
