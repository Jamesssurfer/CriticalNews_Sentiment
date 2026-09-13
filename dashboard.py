"""Render index.html from the sentiment log. Run after scanner.py / daily_rollup.py.

Self-contained static HTML (Chart.js pulled from a CDN) -- safe to serve via
GitHub Pages or just open locally.
"""
import json
from pathlib import Path

import pandas as pd

LOG_PATH = Path(__file__).parent / "data" / "sentiment_log.xlsx"
OUTPUT_PATH = Path(__file__).parent / "index.html"

BUCKET_COLORS = {
    "Middle East Oil Risk": "#C1524B",
    "Macroeconomic Interventions": "#4C8CA8",
    "Trade Wars & Escalations": "#C89B3C",
}

POINTS_PER_SPARKLINE = 42  # ~7 days at 6 runs/day
DAILY_ROWS_SHOWN = 14


def _fmt_score(x) -> str:
    return f"{x:+.2f}" if pd.notna(x) else "n/a"


def _fmt_pct(x) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)) or x == "n/a" or x == "":
        return "n/a"
    try:
        return f"{float(x) * 100:+.1f}%"
    except (TypeError, ValueError):
        return "n/a"


def _bucket_panel_html(bucket: str, log_df: pd.DataFrame) -> str:
    rows = log_df[log_df["bucket"] == bucket].sort_values("timestamp_utc")
    if rows.empty:
        return ""

    latest = rows.iloc[-1]
    prior = rows.iloc[-2] if len(rows) > 1 else None
    color = BUCKET_COLORS.get(bucket, "#8A8F9C")

    delta = None
    if prior is not None:
        delta = latest["composite_score"] - prior["composite_score"]
    delta_html = ""
    if delta is not None:
        arrow = "▲" if delta > 0 else ("▼" if delta < 0 else "—")
        delta_html = f'<span class="delta">{arrow} {delta:+.2f} vs prior run</span>'

    model = latest.get("sentiment_model_used", "finbert")
    model = model if isinstance(model, str) and model in ("vader", "finbert") else "finbert"
    risk_tone = latest["risk_vader_avg"] if model == "vader" else latest["risk_finbert_avg"]
    deesc_tone = latest["deescalation_vader_avg"] if model == "vader" else latest["deescalation_finbert_avg"]

    headlines = []
    for i in (1, 2, 3):
        title = latest.get(f"top_headline_{i}", "")
        url = latest.get(f"top_headline_{i}_url", "")
        if isinstance(title, str) and title.strip():
            headlines.append(f'<li><a href="{url}" target="_blank" rel="noopener">{title}</a></li>')
    headlines_html = "\n".join(headlines) if headlines else "<li class=\"muted\">No articles this run.</li>"

    spark = rows.tail(POINTS_PER_SPARKLINE)
    chart_labels = json.dumps(spark["timestamp_utc"].astype(str).tolist())
    chart_values = json.dumps([round(float(v), 3) for v in spark["composite_score"].tolist()])
    canvas_id = f"chart-{abs(hash(bucket)) % 100000}"

    return f"""
    <section class="panel" style="--accent: {color}">
      <div class="panel-head">
        <h2>{bucket}</h2>
        <span class="timestamp">as of {latest['timestamp_utc']}</span>
      </div>

      <div class="score-row">
        <div class="score">{_fmt_score(latest['composite_score'])}</div>
        {delta_html}
      </div>

      <div class="breakdown">
        <div>
          <span class="label">Risk coverage</span>
          <span class="value">{int(latest['risk_articles'])} articles, tone {_fmt_score(risk_tone)} ({model})</span>
        </div>
        <div>
          <span class="label">De-escalation coverage</span>
          <span class="value">{int(latest['deescalation_articles'])} articles, tone {_fmt_score(deesc_tone)} ({model})</span>
        </div>
      </div>

      <canvas class="sparkline" id="{canvas_id}" height="70"></canvas>
      <script>
        new Chart(document.getElementById("{canvas_id}"), {{
          type: "line",
          data: {{
            labels: {chart_labels},
            datasets: [{{
              data: {chart_values},
              borderColor: "{color}",
              borderWidth: 1.5,
              pointRadius: 0,
              tension: 0.15,
              fill: false,
            }}]
          }},
          options: {{
            responsive: true,
            plugins: {{ legend: {{ display: false }}, tooltip: {{ enabled: false }} }},
            scales: {{ x: {{ display: false }}, y: {{ display: false }} }},
            elements: {{ point: {{ radius: 0 }} }},
          }}
        }});
      </script>

      <div class="headlines">
        <span class="label">Top headlines this run</span>
        <ul>{headlines_html}</ul>
      </div>
    </section>
    """


def _daily_table_html(daily_df: pd.DataFrame) -> str:
    if daily_df.empty:
        return "<p class=\"muted\">No daily rollup yet.</p>"

    recent_dates = sorted(daily_df["date"].unique())[-DAILY_ROWS_SHOWN:]
    daily_df = daily_df[daily_df["date"].isin(recent_dates)]

    buckets = list(BUCKET_COLORS.keys())
    header_cells = "".join(f"<th>{b}</th>" for b in buckets)

    body_rows = []
    for date in recent_dates:
        cells = []
        for bucket in buckets:
            match = daily_df[(daily_df["date"] == date) & (daily_df["bucket"] == bucket)]
            if match.empty:
                cells.append("<td class=\"muted\">—</td>")
                continue
            r = match.iloc[0]
            cells.append(f"<td>{_fmt_score(r['composite_avg'])} <span class=\"muted\">({_fmt_pct(r['composite_pct_change_vs_prev_day'])})</span></td>")
        body_rows.append(f"<tr><td>{date}</td>{''.join(cells)}</tr>")

    return f"""
    <table class="daily-table">
      <thead><tr><th>Date</th>{header_cells}</tr></thead>
      <tbody>{''.join(body_rows)}</tbody>
    </table>
    <p class="muted small">Value shown is the day's average composite score; the figure in
    parentheses is the change versus the prior day for that bucket.</p>
    """


def build_dashboard():
    if not LOG_PATH.exists():
        print(f"No log file at {LOG_PATH} yet -- run scanner.py first.")
        return

    log_df = pd.read_excel(LOG_PATH, sheet_name="Log")
    try:
        daily_df = pd.read_excel(LOG_PATH, sheet_name="Daily")
    except ValueError:
        daily_df = pd.DataFrame()

    last_updated = log_df["timestamp_utc"].max() if not log_df.empty else "n/a"
    panels_html = "\n".join(_bucket_panel_html(b, log_df) for b in BUCKET_COLORS)
    daily_html = _daily_table_html(daily_df)

    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Macro & Geopolitical Risk Sentiment</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.4/chart.umd.min.js"></script>
<style>
  :root {{
    --bg: #14161c;
    --panel: #1b1e27;
    --text: #e7e5de;
    --muted: #8a8f9c;
    --rule: #2a2e3a;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: "IBM Plex Sans", -apple-system, sans-serif;
    line-height: 1.5;
  }}
  .mono {{ font-family: "IBM Plex Mono", ui-monospace, monospace; }}
  header {{
    padding: 2rem 1.5rem 1rem;
    max-width: 880px;
    margin: 0 auto;
    border-bottom: 1px solid var(--rule);
  }}
  header h1 {{
    font-size: 1.4rem;
    font-weight: 600;
    margin: 0 0 0.3rem;
  }}
  header p {{ color: var(--muted); margin: 0; font-size: 0.9rem; }}
  main {{ max-width: 880px; margin: 0 auto; padding: 1.5rem; }}
  .panel {{
    background: var(--panel);
    border-left: 3px solid var(--accent, var(--muted));
    border-top: 1px solid var(--rule);
    border-bottom: 1px solid var(--rule);
    padding: 1.25rem 1.5rem;
    margin-bottom: 1.5rem;
  }}
  .panel-head {{ display: flex; justify-content: space-between; align-items: baseline; flex-wrap: wrap; gap: 0.5rem; }}
  .panel-head h2 {{ font-size: 1.05rem; margin: 0; font-weight: 600; }}
  .timestamp {{ color: var(--muted); font-size: 0.8rem; }}
  .score-row {{ display: flex; align-items: baseline; gap: 0.75rem; margin: 0.6rem 0; }}
  .score {{ font-family: "IBM Plex Mono", ui-monospace, monospace; font-size: 2.1rem; font-weight: 600; }}
  .delta {{ color: var(--muted); font-size: 0.85rem; }}
  .breakdown {{ display: flex; flex-direction: column; gap: 0.25rem; margin-bottom: 0.75rem; font-size: 0.88rem; }}
  .breakdown .label {{ color: var(--muted); margin-right: 0.5rem; }}
  .breakdown .value {{ font-family: "IBM Plex Mono", ui-monospace, monospace; }}
  .sparkline {{ width: 100%; max-height: 70px; margin: 0.5rem 0 0.75rem; }}
  .headlines .label {{ color: var(--muted); font-size: 0.8rem; display: block; margin-bottom: 0.3rem; }}
  .headlines ul {{ margin: 0; padding-left: 1.1rem; font-size: 0.9rem; }}
  .headlines li {{ margin-bottom: 0.25rem; }}
  .headlines a {{ color: var(--text); text-decoration: none; border-bottom: 1px solid var(--rule); }}
  .headlines a:hover {{ border-bottom-color: var(--text); }}
  .muted {{ color: var(--muted); }}
  .small {{ font-size: 0.8rem; }}
  table.daily-table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; margin-top: 0.5rem; }}
  table.daily-table th, table.daily-table td {{
    text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid var(--rule);
    font-family: "IBM Plex Mono", ui-monospace, monospace;
  }}
  table.daily-table th {{ font-family: "IBM Plex Sans", sans-serif; color: var(--muted); font-weight: 500; }}
  footer {{ max-width: 880px; margin: 0 auto; padding: 1rem 1.5rem 2.5rem; color: var(--muted); font-size: 0.78rem; }}
</style>
</head>
<body>
<header>
  <h1>Macro &amp; geopolitical risk sentiment</h1>
  <p>Last scan: {last_updated} UTC · Positive score = escalating pressure · Negative = de-escalating</p>
</header>
<main>
  {panels_html}
  <section class="panel" style="--accent: var(--rule)">
    <div class="panel-head"><h2>Daily history</h2></div>
    {daily_html}
  </section>
</main>
<footer>
  Generated automatically from sentiment_log.xlsx. Composite score is a v1 heuristic
  (article volume x tone, risk side minus de-escalation side) -- treat it as a
  directional read, not a calibrated probability.
</footer>
</body>
</html>
"""
    OUTPUT_PATH.write_text(html, encoding="utf-8")
    print(f"Dashboard written -> {OUTPUT_PATH}")


if __name__ == "__main__":
    build_dashboard()
