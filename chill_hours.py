"""Chill Hours Calculator for backyard fruit trees.

Pulls data from an Ambient Weather Station and computes chill hours
(hours where outdoor temp is between 32-45°F) during nighttime hours
from October 2025 through present.
"""

import json
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import numpy as np
import requests

from dotenv import load_dotenv
load_dotenv()

# --- Constants ---
SEASON_START = datetime(2025, 10, 1)
CHILL_LOW = 32.0   # °F
CHILL_HIGH = 45.0   # °F
NIGHT_START = 12    # noon
NIGHT_END = 12      # noon (next day)
TIMEZONE = "America/Phoenix"
INTERVAL_MINUTES = 5
HOURS_PER_READING = INTERVAL_MINUTES / 60  # 1/12 hour

DATA_DIR = Path("data")
RAW_PARQUET = DATA_DIR / "raw_weather.parquet"
HISTORICAL_CSV = Path("reference_data") / "daily_chill_hours_phoenix.csv"
CHART_DATA_JSON = DATA_DIR / "chart_data.json"
SUMMARY_JSON = DATA_DIR / "summary.json"

# Variety chill hour requirements (sources: Dave Wilson Nursery, Bay Laurel Nursery, urbanfarm.org)
VARIETIES = {
    "🍎 Dorsett Golden": 100,
    "🍑 Desert Gold": 200,
    "🍎 Anna": 200,
    "🍑 Mid-Pride": 250,
    "🍑 Sauzee Swirl": 400,
}


# --- Section A: Data Fetching ---

def _api_get(path, params=None):
    """Make an authenticated GET request to the Ambient Weather API."""
    base = os.environ.get("AMBIENT_ENDPOINT") or "https://rt.ambientweather.net/v1"
    p = {
        "apiKey": os.environ["AMBIENT_API_KEY"],
        "applicationKey": os.environ["AMBIENT_APPLICATION_KEY"],
    }
    if params:
        p.update(params)
    r = requests.get(f"{base}{path}", params=p)
    r.raise_for_status()
    return r.json()


def fetch_weather_data():
    """Fetch weather data from Ambient Weather API with incremental caching."""
    DATA_DIR.mkdir(exist_ok=True)

    devices = _api_get("/devices")
    if not devices:
        raise RuntimeError("No Ambient Weather devices found. Check your API keys.")
    mac = devices[0]["macAddress"]
    name = devices[0].get("info", {}).get("name", mac)
    print(f"Using device: {name}")
    time.sleep(1)  # Rate limit

    # Load existing cache if present
    if RAW_PARQUET.exists():
        cached = pd.read_parquet(RAW_PARQUET)
        cached["dateutc"] = pd.to_numeric(cached["dateutc"], errors="coerce")
        latest_ts = cached["dateutc"].max()
        print(f"Cache has {len(cached)} rows, latest: {datetime.fromtimestamp(latest_ts / 1000, tz=timezone.utc)}")
    else:
        cached = pd.DataFrame()
        latest_ts = int(SEASON_START.timestamp() * 1000)
        print("No cache found, fetching from season start.")

    # Paginate backwards from now
    all_new = []
    end_date = datetime.now(timezone.utc)
    batch_num = 0
    done = False

    while not done:
        batch_num += 1
        end_ms = int(end_date.timestamp() * 1000)
        print(f"  Batch {batch_num}: fetching before {end_date.strftime('%Y-%m-%d %H:%M')} UTC ...", end=" ", flush=True)

        data = _api_get(f"/devices/{mac}", {"limit": 288, "endDate": end_ms})
        time.sleep(2)  # Rate limit: 1 req/sec per key

        if not data:
            print("no data returned, done.")
            break

        df_batch = pd.DataFrame(data)
        df_batch["dateutc"] = pd.to_numeric(df_batch["dateutc"], errors="coerce")
        print(f"got {len(df_batch)} rows.")

        # Check if we've reached already-cached data or season start
        min_ts = df_batch["dateutc"].min()
        if min_ts <= latest_ts and not cached.empty:
            # Keep only rows newer than cache
            df_batch = df_batch[df_batch["dateutc"] > latest_ts]
            if not df_batch.empty:
                all_new.append(df_batch)
            print(f"  Reached cached data. Kept {len(df_batch)} new rows.")
            done = True
        elif min_ts <= int(SEASON_START.timestamp() * 1000):
            all_new.append(df_batch)
            print("  Reached season start.")
            done = True
        else:
            all_new.append(df_batch)
            # Move end_date to oldest timestamp in batch
            end_date = datetime.fromtimestamp(min_ts / 1000, tz=timezone.utc) - timedelta(seconds=1)

    # Combine and deduplicate
    if all_new:
        new_df = pd.concat(all_new, ignore_index=True)
        if not cached.empty:
            combined = pd.concat([cached, new_df], ignore_index=True)
        else:
            combined = new_df
    else:
        combined = cached
        print("No new data fetched.")

    if combined.empty:
        raise RuntimeError("No weather data available.")

    combined = combined.drop_duplicates(subset=["dateutc"]).sort_values("dateutc").reset_index(drop=True)

    # Filter to season start onward
    combined = combined[combined["dateutc"] >= int(SEASON_START.timestamp() * 1000)]

    combined.to_parquet(RAW_PARQUET, index=False)
    print(f"Saved {len(combined)} total rows to {RAW_PARQUET}")
    return combined


# --- Section B: Chill Hours Computation ---

def compute_chill_hours(df):
    """Compute nightly and cumulative chill hours from raw weather data."""
    df = df.copy()

    # Convert UTC ms to timezone-aware local time
    df["timestamp"] = pd.to_datetime(df["dateutc"], unit="ms", utc=True).dt.tz_convert(TIMEZONE)
    df["hour"] = df["timestamp"].dt.hour

    # Flag chill readings (between returns False for NaN, so missing data excluded)
    df["is_chill"] = df["tempf"].between(CHILL_LOW, CHILL_HIGH)

    # Assign night_date (noon-to-noon windows):
    #   Hours 12-23 → that calendar date
    #   Hours 0-11  → previous calendar date
    df["calendar_date"] = df["timestamp"].dt.date

    def assign_night_date(row):
        if NIGHT_START <= row["hour"] <= 23:
            return row["calendar_date"]
        elif 0 <= row["hour"] < NIGHT_END:
            return row["calendar_date"] - timedelta(days=1)
        else:
            return None  # daytime

    df["night_date"] = df.apply(assign_night_date, axis=1)

    # Drop daytime rows
    nights = df.dropna(subset=["night_date"]).copy()
    nights["night_date"] = pd.to_datetime(nights["night_date"])

    # Group by night and compute chill hours
    nightly = (
        nights.groupby("night_date")["is_chill"]
        .sum()
        .mul(HOURS_PER_READING)
        .rename("chill_hours")
        .reset_index()
        .sort_values("night_date")
    )

    nightly["cumulative"] = nightly["chill_hours"].cumsum()
    return nightly


# --- Section B2: Historical Data ---

def load_historical_cumulative():
    """Load historical chill hours CSV and compute per-season cumulative statistics.

    Returns a DataFrame indexed by day_offset (days since Oct 1) with columns
    for median, 25th/75th percentiles, and min/max across all historical seasons.
    Returns None if the CSV doesn't exist.
    """
    if not HISTORICAL_CSV.exists():
        print("No historical data file found, skipping overlay.")
        return None, None

    df = pd.read_csv(HISTORICAL_CSV, parse_dates=["date"])
    df["date"] = df["date"].dt.tz_localize(None).dt.normalize()
    df["month"] = df["date"].dt.month
    df["year"] = df["date"].dt.year

    # Season year = year of the October that starts the season
    df["season_year"] = df["year"].where(df["month"] >= 10, df["year"] - 1)

    # Keep only chill-relevant months (Oct through Mar)
    df = df[df["month"].isin([10, 11, 12, 1, 2, 3])].copy()

    # Day offset from Oct 1 of the season
    df["season_start"] = pd.to_datetime(df["season_year"].astype(int).astype(str) + "-10-01")
    df["day_offset"] = (df["date"] - df["season_start"]).dt.days

    # Compute cumulative chill hours per season
    cumulative_by_season = {}
    for sy, group in df.groupby("season_year"):
        group = group.sort_values("day_offset")
        cumulative_by_season[int(sy)] = group.set_index("day_offset")["chill_hours"].cumsum()

    all_seasons = pd.DataFrame(cumulative_by_season)
    num_seasons = len(all_seasons.columns)

    stats = pd.DataFrame({
        "median": all_seasons.median(axis=1),
        "p25": all_seasons.quantile(0.25, axis=1),
        "p75": all_seasons.quantile(0.75, axis=1),
        "min": all_seasons.min(axis=1),
        "max": all_seasons.max(axis=1),
    })
    print(f"Loaded historical data: {num_seasons} seasons (1975–2024)")
    return stats, all_seasons


def compute_probabilities(current_total, current_day_offset, all_seasons):
    """Estimate probability of reaching each variety's chill hour target.

    Uses historical seasons as empirical analogues, weighted by a Gaussian kernel
    based on how similar each season's trajectory is to the current season at
    the current point in time.

    Returns dict mapping variety name to probability (0.0–1.0), or None if
    no historical data is available.
    """
    if all_seasons is None:
        return None

    # End-of-season reference: day_offset 181 (all 49 seasons have data here)
    ref_offset = 181
    if ref_offset not in all_seasons.index or current_day_offset not in all_seasons.index:
        return None

    # Get cumulative values at current day and at end of season for each historical season
    hist_at_today = all_seasons.loc[current_day_offset].dropna()
    hist_at_end = all_seasons.loc[ref_offset].dropna()

    # Only use seasons that have data at both points
    common = hist_at_today.index.intersection(hist_at_end.index)
    if len(common) < 5:
        return None
    hist_at_today = hist_at_today[common].values
    hist_at_end = hist_at_end[common].values
    n = len(common)

    # Bandwidth: Silverman's rule on remaining hours
    remaining = hist_at_end - hist_at_today
    sigma_remaining = np.std(remaining, ddof=1)
    h = max(1.06 * sigma_remaining * n ** (-0.2), 1.0)

    # Gaussian kernel weights
    weights = np.exp(-0.5 * ((hist_at_today - current_total) / h) ** 2)

    # ESS floor: widen bandwidth if effective sample size < 5
    for _ in range(20):
        ess = weights.sum() ** 2 / (weights ** 2).sum() if weights.sum() > 0 else 0
        if ess >= 5:
            break
        h *= 1.5
        weights = np.exp(-0.5 * ((hist_at_today - current_total) / h) ** 2)

    total_weight = weights.sum()
    if total_weight == 0:
        return None

    # Compute weighted probability for each variety target
    probabilities = {}
    for name, target in VARIETIES.items():
        if current_total >= target:
            probabilities[name] = 1.0
        else:
            reached = (hist_at_end >= target).astype(float)
            probabilities[name] = np.dot(weights, reached) / total_weight

    return probabilities


# --- Section C: Output ---

def print_summary(nightly, probabilities=None):
    """Print summary statistics and recent nightly table."""
    total = nightly["cumulative"].iloc[-1] if len(nightly) > 0 else 0
    num_nights = len(nightly)
    avg = nightly["chill_hours"].mean() if num_nights > 0 else 0
    max_night = nightly["chill_hours"].max() if num_nights > 0 else 0

    print("\n" + "=" * 50)
    print("  CHILL HOURS SUMMARY")
    print("=" * 50)
    print(f"  Season start:        {SEASON_START.strftime('%Y-%m-%d')}")
    print(f"  Total chill hours:   {total:.1f}")
    print(f"  Number of nights:    {num_nights}")
    print(f"  Average per night:   {avg:.2f}")
    print(f"  Max single night:    {max_night:.2f}")
    print("=" * 50)

    # Probability table
    if probabilities:
        print(f"\n  {'Variety':<20} {'Target':>10} {'Prob':>8} {'Status':>10}")
        print(f"  {'-'*20} {'-'*10} {'-'*8} {'-'*10}")
        for name, target in sorted(VARIETIES.items(), key=lambda x: x[1]):
            prob = probabilities[name]
            pct = f"{prob * 100:.0f}%"
            if total >= target:
                status = "MET"
            elif prob >= 0.90:
                status = "Likely"
            elif prob >= 0.50:
                status = "Possible"
            elif prob >= 0.10:
                status = "Unlikely"
            else:
                status = "Very Low"
            print(f"  {name:<20} {target:>7} hrs {pct:>8} {status:>10}")
        print()

    # Last 14 nights
    recent = nightly.tail(14).copy()
    if recent.empty:
        print("\nNo nightly data to display.")
        return

    print(f"\n  Last {len(recent)} nights:")
    print(f"  {'Night Date':<14} {'Chill Hrs':>10} {'Cumulative':>12}")
    print(f"  {'-'*14} {'-'*10} {'-'*12}")
    for _, row in recent.iterrows():
        date_str = row["night_date"].strftime("%Y-%m-%d")
        print(f"  {date_str:<14} {row['chill_hours']:>10.2f} {row['cumulative']:>12.1f}")
    print()


def write_chart_data_json(nightly, historical=None, probabilities=None):
    """Export chart data as JSON for the D3.js visualization."""
    if nightly.empty:
        print("No data to export for chart.")
        return

    DATA_DIR.mkdir(exist_ok=True)

    season_start = pd.Timestamp(f"{SEASON_START.year}-10-01")
    xlim_left = pd.Timestamp(f"{SEASON_START.year}-11-01")
    xlim_right = pd.Timestamp(f"{SEASON_START.year + 1}-03-31")
    last_observed = nightly["night_date"].max()
    current_total = nightly["cumulative"].iloc[-1]

    # Nightly data
    nightly_data = []
    for _, row in nightly.iterrows():
        nightly_data.append({
            "date": row["night_date"].strftime("%Y-%m-%d"),
            "chill_hours": round(float(row["chill_hours"]), 2),
            "cumulative": round(float(row["cumulative"]), 2),
        })

    # Historical data: map day_offset to calendar dates, filter to display range
    historical_data = []
    if historical is not None:
        for day_offset in historical.index:
            hist_date = season_start + pd.Timedelta(days=int(day_offset))
            if hist_date < xlim_left or hist_date > xlim_right:
                continue
            historical_data.append({
                "date": hist_date.strftime("%Y-%m-%d"),
                "min": round(float(historical.loc[day_offset, "min"]), 2),
                "p25": round(float(historical.loc[day_offset, "p25"]), 2),
                "median": round(float(historical.loc[day_offset, "median"]), 2),
                "p75": round(float(historical.loc[day_offset, "p75"]), 2),
                "max": round(float(historical.loc[day_offset, "max"]), 2),
            })

    # Varieties: group by threshold (same logic as old plot_chill_hours)
    by_hours = defaultdict(list)
    for name, hrs in VARIETIES.items():
        by_hours[hrs].append(name)
    varieties_data = []
    for hrs, names in sorted(by_hours.items()):
        met = bool(current_total >= hrs)
        entry = {
            "names": names,
            "label": ", ".join(names),
            "target": hrs,
            "met": met,
        }
        if not met and probabilities:
            prob = probabilities.get(names[0])
            if prob is not None:
                entry["probability"] = round(float(prob), 2)
        varieties_data.append(entry)

    chart_data = {
        "season": "2025-2026",
        "season_start": season_start.strftime("%Y-%m-%d"),
        "xlim_left": xlim_left.strftime("%Y-%m-%d"),
        "xlim_right": xlim_right.strftime("%Y-%m-%d"),
        "last_observed": last_observed.strftime("%Y-%m-%d"),
        "nightly": nightly_data,
        "historical": historical_data,
        "varieties": varieties_data,
    }

    CHART_DATA_JSON.write_text(json.dumps(chart_data, indent=2))
    print(f"Chart data written to {CHART_DATA_JSON}")


def write_summary_json(nightly, probabilities=None):
    """Write a JSON summary of chill hours stats for the web page."""
    total = nightly["cumulative"].iloc[-1] if len(nightly) > 0 else 0
    num_nights = len(nightly)
    avg = nightly["chill_hours"].mean() if num_nights > 0 else 0
    last_date = nightly["night_date"].max().strftime("%Y-%m-%d") if num_nights > 0 else None

    varieties = []
    for name, target in sorted(VARIETIES.items(), key=lambda x: x[1]):
        entry = {"name": name, "target": int(target), "met": bool(total >= target)}
        if probabilities and name in probabilities:
            entry["probability"] = round(probabilities[name], 3)
        varieties.append(entry)

    summary = {
        "total_chill_hours": round(total, 1),
        "nights_tracked": num_nights,
        "avg_per_night": round(avg, 2),
        "last_updated": last_date,
        "season": "2025-2026",
        "varieties": varieties,
    }

    DATA_DIR.mkdir(exist_ok=True)
    SUMMARY_JSON.write_text(json.dumps(summary, indent=2))
    print(f"Summary written to {SUMMARY_JSON}")


# --- Section D: Main ---

def main():
    df = fetch_weather_data()
    nightly = compute_chill_hours(df)
    historical_stats, all_seasons = load_historical_cumulative()

    # Compute Bayesian probability estimates for reaching variety targets
    probabilities = None
    if all_seasons is not None and len(nightly) > 0:
        current_total = nightly["cumulative"].iloc[-1]
        last_night = nightly["night_date"].max()
        season_start = pd.Timestamp(f"{SEASON_START.year}-10-01")
        current_day_offset = (last_night - season_start).days
        probabilities = compute_probabilities(current_total, current_day_offset, all_seasons)

    print_summary(nightly, probabilities)
    write_chart_data_json(nightly, historical_stats, probabilities)
    write_summary_json(nightly, probabilities)


if __name__ == "__main__":
    main()
