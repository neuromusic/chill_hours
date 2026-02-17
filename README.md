# Chill Hours Tracker

Tracks accumulated [chill hours](https://en.wikipedia.org/wiki/Chilling_requirement) from a backyard weather station in Phoenix, AZ for the 2025–2026 winter season. Chill hours are the number of hours where the temperature is between 32–45°F — fruit trees need a minimum number to produce well.

**Live dashboard:** https://neuromusic.github.io/chill_hours/

## How it works

A Python script pulls 5-minute temperature readings from an [Ambient Weather](https://ambientweather.net/) station, counts overnight hours in the chill range (noon-to-noon windows), and compares the current season against 49 years of Phoenix historical data (1975–2024). It estimates the probability of reaching each fruit tree variety's chill requirement using kernel-weighted historical analogues.

A GitHub Actions workflow runs this daily at 3:00 AM MST and publishes the results to GitHub Pages.

## Tracked varieties

| Variety | Chill Hours Needed |
|---------|--------------------|
| Dorsett Golden (apple) | 100 |
| Desert Gold (peach) | 200 |
| Anna (apple) | 200 |
| Mid-Pride (peach) | 250 |
| Sauzee Swirl (pluot) | 400 |

## Local setup

Requires Python 3.12+ and an Ambient Weather account with API keys.

```bash
pip install -r requirements.txt
```

Create a `.env` file:

```
AMBIENT_API_KEY=your_key
AMBIENT_APPLICATION_KEY=your_app_key
AMBIENT_ENDPOINT=https://rt.ambientweather.net/v1
```

Run:

```bash
python chill_hours.py
```

First run fetches the full season (~7 minutes due to API rate limiting). Subsequent runs are incremental and take seconds.

Outputs are written to `data/`:
- `raw_weather.parquet` — cached API responses
- `chill_hours.png` — two-panel chart
- `summary.json` — structured stats for the web page
