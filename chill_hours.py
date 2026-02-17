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
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
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
CHART_PATH = DATA_DIR / "chill_hours.png"
SUMMARY_JSON = DATA_DIR / "summary.json"

# Variety chill hour requirements (sources: Dave Wilson Nursery, Bay Laurel Nursery, urbanfarm.org)
VARIETIES = {
    "Dorsett Golden": 100,
    "Desert Gold": 200,
    "Anna": 200,
    "Mid-Pride": 250,
    "Sauzee Swirl": 400,
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


def plot_chill_hours(nightly, historical=None, probabilities=None):
    """Generate two-panel chart: nightly bars + cumulative line with historical overlay."""
    if nightly.empty:
        print("No data to plot.")
        return

    DATA_DIR.mkdir(exist_ok=True)

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 9), sharex=True)
    fig.suptitle("Chill Hours — Winter 2025–2026", fontsize=14, fontweight="bold")

    # Shared x-axis range: Nov 1 through Mar 31
    season_start = pd.Timestamp(f"{SEASON_START.year}-10-01")
    xlim_left = pd.Timestamp(f"{SEASON_START.year}-11-01")
    xlim_right = pd.Timestamp(f"{SEASON_START.year + 1}-03-31")

    dates = nightly["night_date"]
    last_observed = dates.max()

    # Top panel: nightly bar chart
    colors = ["#4a90d9" if h > 0 else "#cccccc" for h in nightly["chill_hours"]]
    ax1.bar(dates, nightly["chill_hours"], color=colors, width=0.8)
    # Shade unobserved future nights
    ax1.axvspan(last_observed + pd.Timedelta(days=1), xlim_right,
                color="#f0f0f0", zorder=0)
    ax1.axvline(last_observed + pd.Timedelta(hours=12), color="#aaaaaa",
                linewidth=1, linestyle="--", alpha=0.6)
    mid_future = last_observed + (xlim_right - last_observed) / 2
    ax1.text(mid_future, 0.92, "Not yet observed",
             transform=ax1.get_xaxis_transform(),
             fontsize=9, color="#999999", ha="center", va="top")
    ax1.set_ylabel("Chill Hours per Night")
    ax1.set_title("Daily Chill Hours (32–45°F, noon–noon)")
    ax1.grid(axis="y", alpha=0.3)

    # Bottom panel: cumulative with historical overlay

    if historical is not None:
        # Map historical day offsets to current season dates for aligned x-axis
        hist_dates = season_start + pd.to_timedelta(historical.index, unit="D")
        ax2.fill_between(hist_dates, historical["min"], historical["max"],
                         alpha=0.08, color="#888888", label="Historical range (1975–2024)")
        ax2.fill_between(hist_dates, historical["p25"], historical["p75"],
                         alpha=0.2, color="#888888", label="25th–75th percentile")
        ax2.plot(hist_dates, historical["median"], color="#888888",
                 linewidth=1.5, linestyle="--", label="Historical median")

    ax2.plot(dates, nightly["cumulative"], color="#d94a4a", linewidth=2.5,
             label="2025–26 season")
    ax2.fill_between(dates, nightly["cumulative"], alpha=0.15, color="#d94a4a")

    # Variety chill hour thresholds
    current_total = nightly["cumulative"].iloc[-1] if len(nightly) > 0 else 0
    # Group varieties at the same threshold to stack labels
    by_hours = defaultdict(list)
    for name, hrs in VARIETIES.items():
        by_hours[hrs].append(name)
    for hrs, names in sorted(by_hours.items()):
        met = current_total >= hrs
        color = "#2d8632" if met else "#b8860b"
        symbol = "\u2714" if met else "\u2022"
        label = ", ".join(names)
        ax2.axhline(y=hrs, color=color, linewidth=1, linestyle=":", alpha=0.7)
        # Build annotation text, appending probability for unmet targets
        ann_text = f" {symbol} {label} ({hrs} hrs)"
        if not met and probabilities:
            # Use the probability of the first variety at this threshold
            prob = probabilities.get(names[0])
            if prob is not None:
                ann_text += f" \u2014 {prob * 100:.0f}%"
        ax2.annotate(ann_text,
                     xy=(1.01, hrs), xycoords=("axes fraction", "data"),
                     va="center", ha="left", fontsize=8, color=color,
                     fontweight="bold", clip_on=False)

    # Shade unobserved region on cumulative panel too
    ax2.axvspan(last_observed + pd.Timedelta(days=1), xlim_right,
                color="#f0f0f0", zorder=0)
    ax2.axvline(last_observed + pd.Timedelta(hours=12), color="#aaaaaa",
                linewidth=1, linestyle="--", alpha=0.6)

    ax2.set_ylabel("Cumulative Chill Hours")
    ax2.set_title("Cumulative Chill Hours vs. Historical")
    ax2.set_xlabel("Date")
    ax2.grid(axis="y", alpha=0.3)
    ax2.legend(loc="upper left", fontsize=9)

    # Shared x-axis formatting
    ax2.set_xlim(xlim_left, xlim_right)
    ax2.xaxis.set_major_locator(mdates.MonthLocator())
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b '%y"))
    ax2.minorticks_off()
    plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45, ha="right")

    fig.subplots_adjust(hspace=0.4, right=0.75)
    fig.savefig(CHART_PATH, dpi=150)
    print(f"Chart saved to {CHART_PATH}")
    plt.close(fig)


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
    plot_chill_hours(nightly, historical_stats, probabilities)
    write_summary_json(nightly, probabilities)


if __name__ == "__main__":
    main()
