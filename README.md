# News Sentiment Scanner

Tracks three risk buckets from live news, every 4 hours, dual-scored with FinBERT
and VADER, logged to `data/sentiment_log.xlsx`, rendered to `index.html`.

- **Middle East Oil Risk**
- **Macroeconomic Interventions**
- **Trade Wars & Escalations**

## How it works

- `config.py` — the queries that define each bucket. Each bucket has a
  `risk_queries` set and a `deescalation_queries` set. See the comment at the
  top of that file for why both exist (short version: a risk query going quiet
  reads as "nothing happening," not "improving" — the de-escalation queries are
  what let the scanner actually detect calming).
- `news_fetcher.py` — pulls each query's Google News RSS feed, dedupes by URL,
  extracts the lede paragraph of each article with `trafilatura` (falls back to
  the headline alone if the page can't be fetched — paywalls and bot-blocking
  are expected and common).
- `sentiment_engine.py` — scores title+lede with both FinBERT and VADER,
  logged as two separate numbers, not fused into one.
- `scanner.py` — one scan cycle: fetch + score both query groups per bucket,
  compute a composite score, append one row per bucket to the Excel log.
- `daily_rollup.py` — rebuilds a `Daily` sheet from the `Log` sheet: one row
  per (date, bucket), with a live Excel formula for day-over-day % change.
- `dashboard.py` — renders `index.html` from the current log: per-bucket score,
  trend sparkline, top 3 headlines, and a daily history table.

## Composite score

```
risk_weight    = risk_articles * (0.5 - risk_finbert_avg / 2)
deescal_weight = deescalation_articles * (0.5 + deescalation_finbert_avg / 2)
composite      = risk_weight - deescal_weight
```

Positive = net escalation pressure. Negative = net de-escalation. Zero = quiet
on both sides. **This is a v1 heuristic**, not a validated model — reasonable
starting weights to look at and tune once you have a few weeks of real data.

## Setting it up

1. Push this repo (replacing the original two-file version) to GitHub, on the
   `main` branch.
2. In **Settings → Actions → General**, confirm "Workflow permissions" allows
   "Read and write permissions" — the workflows commit the updated log and
   dashboard back to the repo, so this must be on.
3. Nothing else needs a secret or API key — Google News RSS and article pages
   are fetched anonymously.
4. Trigger a first run manually: **Actions → Scan news sentiment → Run workflow**.
   Check that `data/sentiment_log.xlsx` and `index.html` show up in the repo
   afterward.
5. (Optional) Enable GitHub Pages on this repo pointed at the root of `main` to
   get `index.html` served at a real URL instead of only viewable via download.

## Known limitations / things worth knowing before you trust the numbers

- **Scheduling isn't exact.** GitHub does not guarantee scheduled workflows run
  at the exact minute — expect occasional delays of a few minutes, more when
  GitHub's runners are under load.
- **The daily rollup uses UTC calendar days**, not Singapore time. The last
  scan of a UTC day lands at 20:05 UTC (04:05 SGT); the rollup runs at
  20:45 UTC. If you want rollups aligned to your own trading day instead,
  the fix is to change the `date` derivation in `scanner.py` to a fixed
  UTC+8 offset and shift both cron schedules — worth doing once you've seen
  a couple of weeks of real output and know if the UTC framing bothers you.
- **The Excel log is appended to directly** (`openpyxl` opens and re-saves the
  whole file each run). That's simple and matches what you asked for, but it
  means every run rewrites the whole binary file, so the git history for this
  repo will be one heavy diff every 4 hours forever. If that becomes annoying,
  moving to CSV-as-source-of-truth with the `.xlsx` generated on demand is the
  standard fix — not built here since you asked for direct append.
- **Google News RSS scraping from a shared GitHub-hosted runner IP, six times a
  day, indefinitely, is a rate-limit/blocking risk that grows over time.** Not
  a problem on day one; worth watching `risk_articles` / `deescalation_articles`
  counts for an unexplained drop toward zero across all buckets at once, which
  would point at throttling rather than an actual quiet news cycle.
- **FinBERT is tuned on financial/analyst-report language, not military or
  diplomatic language.** VADER is logged alongside it specifically so you can
  see when they disagree rather than trusting either blindly.
- Article counts per query are capped at `MAX_ARTICLES_PER_QUERY` (8, in
  `config.py`) to keep each run's runtime and fetch volume bounded.
