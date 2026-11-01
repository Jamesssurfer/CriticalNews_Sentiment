"""Render index.html from the sentiment log. Run after scanner.py / daily_rollup.py.

Self-contained static HTML -- Chart.js is vendored in vendor/chart.umd.js
rather than loaded from a CDN (a CDN was silently blocked on a real network
once, with no visible error to the person looking at the page -- the chart
area just stayed blank. Self-hosting removes that failure mode entirely).
"""
import json
from collections import OrderedDict
from pathlib import Path

import numpy as np
import pandas as pd

from config import (
    CORR_MIN_DAYS, PRICE_SIGNALS, PRIMARY_COLOR, PRIMARY_LABEL, PRIMARY_TICKER,
    ROLL_JUMP_PCT, SIGNAL_MIN_DAYS, SIGNAL_SIGMA, SIGNAL_WINDOW_DAYS,
)

LOG_PATH = Path(__file__).parent / "data" / "sentiment_log.xlsx"
OUTPUT_PATH = Path(__file__).parent / "index.html"

BUCKET_COLORS = {
    "Middle East Oil Risk": "#C1524B",
    "Macroeconomic Interventions": "#4C8CA8",
    "Trade Wars & Escalations": "#C89B3C",
}

POINTS_PER_RUN_CHART = 24  # rolling 4 days at 6 runs/day
DAILY_CHART_DAYS = 30  # daily-score chart per bucket -- shows fewer if less history exists yet


def _fetch_daily_closes(ticker: str, first_date: str, last_date: str) -> dict:
    """Daily closes from yfinance as {"YYYY-MM-DD": close}. Returns {} on any
    failure (yfinance missing, Yahoo unreachable, empty frame) so the dashboard
    still builds -- that line is simply omitted and the status says so."""
    try:
        import yfinance as yf

        start = (pd.Timestamp(first_date) - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
        end = (pd.Timestamp(last_date) + pd.Timedelta(days=2)).strftime("%Y-%m-%d")
        hist = yf.Ticker(ticker).history(start=start, end=end, interval="1d", auto_adjust=False)
        if hist is None or hist.empty:
            print(f"yfinance returned no rows for {ticker}; line omitted.")
            return {}
        out = {d.strftime("%Y-%m-%d"): float(v) for d, v in hist["Close"].dropna().items()}
        # ^TNX has been quoted both as yield x10 (42.5) and as yield (4.25).
        if ticker == "^TNX" and out and float(np.median(list(out.values()))) > 20:
            out = {k: v / 10 for k, v in out.items()}
        return out
    except Exception as exc:  # network, import, parsing -- never block the build
        print(f"Price fetch failed for {ticker} ({exc!r}); line omitted.")
        return {}


def _fmt_score(x) -> str:
    return f"{x:+.2f}" if pd.notna(x) else "n/a"


def _fmt_pct(x) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)) or x == "n/a" or x == "":
        return "n/a"
    try:
        return f"{float(x) * 100:+.1f}%"
    except (TypeError, ValueError):
        return "n/a"


def _five_day_status(bucket_daily: pd.DataFrame, px: dict, expected_sign: int) -> dict:
    """Compare the composite's 5-trading-day change with the price's, on dates
    where both exist. Confirming/Diverging only when BOTH moved more than
    SIGNAL_SIGMA std devs of their own history of 5-day changes; otherwise No signal."""
    n = SIGNAL_WINDOW_DAYS
    if not px or bucket_daily.empty:
        return {"state": "no_data"}
    f = bucket_daily[["date", "composite_avg"]].copy()
    f["date"] = f["date"].astype(str)
    f["px"] = f["date"].map(px)
    f = f.dropna().sort_values("date").reset_index(drop=True)
    if len(f) < SIGNAL_MIN_DAYS:
        return {"state": "insufficient", "have": len(f)}

    dc = f["composite_avg"].diff(n)
    dp = f["px"].pct_change(n) * 100
    obs = pd.DataFrame({"dc": dc, "dp": dp}).dropna()
    sc, sp = float(obs["dc"].std()), float(obs["dp"].std())
    last_dc, last_dp = float(dc.iloc[-1]), float(dp.iloc[-1])
    c_big = abs(last_dc) > SIGNAL_SIGMA * sc
    p_big = abs(last_dp) > SIGNAL_SIGMA * sp
    aligned = expected_sign * np.sign(last_dc) * np.sign(last_dp) > 0
    state = "no_signal"
    if c_big and p_big:
        state = "confirming" if aligned else "diverging"

    corr = None
    if len(f) >= CORR_MIN_DAYS:
        rows = list(range(len(f) - 1, -1, -n))[::-1]  # non-overlapping 5-day windows ending at the latest day
        sub = f.iloc[rows]
        cc = sub["composite_avg"].diff().dropna()
        pp = sub["px"].pct_change().dropna()
        if len(cc) >= 4:
            corr = float(cc.reset_index(drop=True).corr(pp.reset_index(drop=True)))

    return {
        "state": state, "date": f["date"].iloc[-1], "days": len(f),
        "dc": last_dc, "dp": last_dp, "sc": sc, "sp": sp, "corr": corr,
    }


def _series_change(px: dict, unit: str):
    """Latest 5-trading-day change of a price series: % for prices, bp for yields."""
    s = pd.Series(px).sort_index()
    n = SIGNAL_WINDOW_DAYS
    if len(s) <= n:
        return None
    if unit == "bp":
        return float((s.iloc[-1] - s.iloc[-1 - n]) * 100)
    return float((s.iloc[-1] / s.iloc[-1 - n] - 1) * 100)


def _regime_label(dxy: dict, tnx: dict) -> str:
    """Hawkish vs dovish read from DXY and the 10Y yield moving together over 5 days."""
    n = SIGNAL_WINDOW_DAYS
    f = pd.DataFrame({"dxy": pd.Series(dxy), "y": pd.Series(tnx)}).dropna().sort_index()
    if len(f) <= n:
        return "n/a (not enough price history)"
    d_dxy = f["dxy"].iloc[-1] - f["dxy"].iloc[-1 - n]
    d_y = f["y"].iloc[-1] - f["y"].iloc[-1 - n]
    if d_dxy > 0 and d_y > 0:
        return "Hawkish regime (DXY up, 10Y yield up)"
    if d_dxy < 0 and d_y < 0:
        return "Dovish / liquidity regime (DXY down, 10Y yield down)"
    return "Mixed (DXY and 10Y yield moving opposite ways)"


def _signal_html(status: dict, cfg: dict, ctx_change, regime) -> str:
    sign = cfg["expected_sign"]
    sign_txt = f"{sign:+d} (DXY {'rises with' if sign > 0 else 'falls as'} the score)"
    state = status.get("state")
    heads = {
        "confirming": ("Confirming", "confirming"),
        "diverging": ("Diverging", "diverging"),
        "no_signal": ("No signal", "quiet"),
        "insufficient": ("Not enough data", "quiet"),
        "no_data": ("No price data", "quiet"),
    }
    badge, cls = heads[state]
    lines = []
    if state in ("confirming", "diverging", "no_signal"):
        corr = status["corr"]
        corr_txt = (
            f"{corr:+.2f} (realized sign {'+' if corr > 0 else '-'})" if corr is not None
            else f"insufficient data ({status['days']} of {CORR_MIN_DAYS} days)"
        )
        explain = {
            "confirming": "Score and DXY both moved beyond their normal 5-day range, in the expected direction.",
            "diverging": "Score and DXY both moved beyond their normal 5-day range, against the expected direction. "
                         "A mismatch, not a forecast -- news can trail price.",
            "no_signal": "At least one series is inside its normal 5-day range.",
        }[state]
        lines.append(explain)
        lines.append(
            f"Composite 5-day change {status['dc']:+.2f} (normal range \u00b1{SIGNAL_SIGMA * status['sc']:.2f}) &middot; "
            f"{PRIMARY_LABEL} 5-day change {status['dp']:+.2f}% (normal range \u00b1{SIGNAL_SIGMA * status['sp']:.2f}%)"
        )
        lines.append(f"Expected sign {sign_txt} &middot; Realized correlation {corr_txt}")
        asof = f"as of {status['date']}"
    elif state == "insufficient":
        lines.append(f"Needs {SIGNAL_MIN_DAYS} aligned trading days of score and price; have {status['have']}.")
        asof = ""
    else:
        lines.append(f"{PRIMARY_TICKER} prices were unavailable this build, so no comparison was made.")
        asof = ""

    if ctx_change is not None:
        unit = "bp" if cfg["context_unit"] == "bp" else "%"
        lines.append(f"{cfg['context_label']} 5-day change: {ctx_change:+.1f}{unit}")
    if regime:
        lines.append(f"Regime: {regime}")

    body = "".join(f"<div>{ln}</div>" for ln in lines)
    return (
        f'<div class="signal signal-{cls}"><div class="signal-head">'
        f'<span class="badge">{badge}</span><span class="muted small">{asof} &middot; 5-day windows, '
        f'normal range = {SIGNAL_SIGMA:g} std dev of own history</span></div>'
        f'<div class="signal-body mono">{body}</div></div>'
    )


def _chart_series(px: dict, dates: list, unit: str):
    """(change-since-window-start, raw close) per chart date. None where no close."""
    vals = [px.get(d) for d in dates]
    base = next((v for v in vals if v is not None), None)
    if base is None:
        return [None] * len(dates), [None] * len(dates)
    if unit == "bp":
        ch = [round((v - base) * 100, 1) if v is not None else None for v in vals]
    else:
        ch = [round((v / base - 1) * 100, 3) if v is not None else None for v in vals]
    raw = [round(v, 3) if v is not None else None for v in vals]
    return ch, raw


def _roll_flags(px: dict, dates: list, ticker: str) -> list:
    thresh = ROLL_JUMP_PCT.get(ticker)
    if not thresh or not px:
        return [False] * len(dates)
    s = pd.Series(px).sort_index()
    jumps = (s.pct_change().abs() * 100) > thresh
    flagged = set(jumps[jumps].index)
    return [d in flagged for d in dates]


PX_CHART_JS = """
(function () {
  var rollFlags = __ROLL__;
  var raws = [null, __DXY_RAW__, __THIRD_RAW__];
  var units = ["", "%", "__THIRD_UNIT__"];
  new Chart(document.getElementById("__CANVAS__"), {
    type: "line",
    data: {
      labels: __LABELS__,
      datasets: [
        { label: "Composite", data: __COMPOSITE__, yAxisID: "yScore", borderColor: "__COLOR__",
          backgroundColor: "__COLOR__", borderWidth: 1.5, pointRadius: 2, tension: 0.15, fill: false },
        { label: "__DXY_LABEL__", data: __DXY__, yAxisID: "yPrice", borderColor: "__DXY_COLOR__",
          backgroundColor: "__DXY_COLOR__", borderWidth: 1.5, pointRadius: 2, tension: 0.15, spanGaps: true, fill: false },
        { label: "__THIRD_LABEL__", data: __THIRD__, yAxisID: "__THIRD_AXIS__", borderColor: "__THIRD_COLOR__",
          backgroundColor: "__THIRD_COLOR__", borderWidth: 1.5, borderDash: [5, 3], pointRadius: 2, tension: 0.15,
          spanGaps: true, fill: false },
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: true, labels: { color: "#8a8f9c", boxWidth: 12, font: { family: "IBM Plex Mono", size: 11 } } },
        tooltip: {
          callbacks: {
            label: function (ctx) {
              var v = ctx.parsed.y;
              if (v === null || v === undefined) return ctx.dataset.label + ": n/a";
              var raw = raws[ctx.datasetIndex];
              var tail = raw && raw[ctx.dataIndex] !== null ? "  (close " + raw[ctx.dataIndex] + ")" : "";
              return ctx.dataset.label + ": " + v.toFixed(2) + units[ctx.datasetIndex] + tail;
            },
            footer: function (items) {
              for (var i = 0; i < items.length; i++) {
                if (items[i].datasetIndex === 2 && rollFlags[items[i].dataIndex]) {
                  return "Large 1-day jump on this futures series (possible contract roll)";
                }
              }
              return "";
            }
          }
        },
      },
      scales: {
        x: { ticks: { color: "#8a8f9c", maxTicksLimit: 8, font: { family: "IBM Plex Mono", size: 10 } }, grid: { color: "#2a2e3a" } },
        yScore: {
          position: "left",
          title: { display: true, text: "Daily composite", color: "__COLOR__", font: { size: 11 } },
          ticks: { color: "__COLOR__", font: { family: "IBM Plex Mono", size: 10 } },
          grid: { color: "#2a2e3a" },
        },
        yPrice: {
          position: "right",
          title: { display: true, text: "__PRICE_AXIS_TITLE__", color: "__DXY_COLOR__", font: { size: 11 } },
          ticks: { color: "__DXY_COLOR__", font: { family: "IBM Plex Mono", size: 10 }, callback: function (v) { return v + "%"; } },
          grid: { drawOnChartArea: false },
        },
        __EXTRA_SCALE__
      },
    }
  });
})();
"""

EXTRA_YIELD_SCALE = """yYield: {
          position: "right",
          title: { display: true, text: "10Y yield change (bp)", color: "__THIRD_COLOR__", font: { size: 11 } },
          ticks: { color: "__THIRD_COLOR__", font: { family: "IBM Plex Mono", size: 10 } },
          grid: { drawOnChartArea: false },
        },"""


def _bucket_panel_html(bucket: str, log_df: pd.DataFrame, daily_df: pd.DataFrame, prices: dict, active: bool = False) -> str:
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

    def _headline_list(prefix: str) -> str:
        items = []
        for i in (1, 2, 3):
            title = latest.get(f"{prefix}{i}", "")
            url = latest.get(f"{prefix}{i}_url", "")
            if isinstance(title, str) and title.strip():
                items.append(f'<li><a href="{url}" target="_blank" rel="noopener">{title}</a></li>')
        return "\n".join(items) if items else "<li class=\"muted\">No articles this run.</li>"

    risk_headlines_html = _headline_list("top_headline_")
    deescalation_headlines_html = _headline_list("deescalation_top_headline_")

    chart_rows = rows.tail(POINTS_PER_RUN_CHART)
    chart_times = pd.to_datetime(chart_rows["timestamp_utc"])
    chart_labels = json.dumps(chart_times.dt.strftime("%m-%d %Hh").tolist())
    chart_values = json.dumps([round(float(v), 3) for v in chart_rows["composite_score"].tolist()])
    zero_line = json.dumps([0] * len(chart_rows))
    canvas_id = f"chart-4h-{abs(hash(bucket)) % 100000}"

    daily_rows = daily_df[daily_df["bucket"] == bucket].sort_values("date").tail(DAILY_CHART_DAYS) if not daily_df.empty else daily_df
    daily_labels = json.dumps(daily_rows["date"].astype(str).tolist()) if not daily_rows.empty else "[]"
    daily_values = json.dumps([round(float(v), 3) for v in daily_rows["composite_avg"].tolist()]) if not daily_rows.empty else "[]"
    daily_zero_line = json.dumps([0] * len(daily_rows)) if not daily_rows.empty else "[]"
    daily_canvas_id = f"chart-daily-{abs(hash(bucket)) % 100000}"

    # Daily composite vs DXY (+ per-bucket context series). Chart dates = the daily chart's dates.
    cfg = PRICE_SIGNALS[bucket]
    dxy_px = prices.get(PRIMARY_TICKER, {})
    ctx_px = prices.get(cfg["context_ticker"], {})
    unit = cfg["context_unit"]
    px_dates = daily_rows["date"].astype(str).tolist() if not daily_rows.empty else []
    dxy_ch, dxy_raw = _chart_series(dxy_px, px_dates, "%")
    ctx_ch, ctx_raw = _chart_series(ctx_px, px_dates, unit)
    bucket_daily = daily_df[daily_df["bucket"] == bucket] if not daily_df.empty else daily_df
    status = _five_day_status(bucket_daily, dxy_px, cfg["expected_sign"])
    regime = _regime_label(dxy_px, ctx_px) if cfg.get("regime") and dxy_px and ctx_px else None
    signal_html = _signal_html(status, cfg, _series_change(ctx_px, unit) if ctx_px else None, regime)
    missing = [n for n, d in ((PRIMARY_LABEL, dxy_px), (cfg["context_label"], ctx_px)) if not d]
    px_note = f' <span class="muted">(unavailable this build: {", ".join(missing)})</span>' if missing else ""
    px_title = f"Daily composite vs {PRIMARY_LABEL} and {cfg['context_label']}, last {len(px_dates)} days{px_note}"
    third_axis = "yYield" if unit == "bp" else "yPrice"
    px_script = PX_CHART_JS.replace("__EXTRA_SCALE__", EXTRA_YIELD_SCALE if unit == "bp" else "")
    for token, value in {
        "__CANVAS__": f"chart-px-{abs(hash(bucket)) % 100000}",
        "__LABELS__": json.dumps(px_dates),
        "__COMPOSITE__": json.dumps([round(float(v), 3) for v in daily_rows["composite_avg"].tolist()]) if px_dates else "[]",
        "__DXY__": json.dumps(dxy_ch), "__DXY_RAW__": json.dumps(dxy_raw),
        "__THIRD__": json.dumps(ctx_ch), "__THIRD_RAW__": json.dumps(ctx_raw),
        "__ROLL__": json.dumps(_roll_flags(ctx_px, px_dates, cfg["context_ticker"])),
        "__COLOR__": color, "__DXY_COLOR__": PRIMARY_COLOR, "__DXY_LABEL__": PRIMARY_LABEL,
        "__THIRD_COLOR__": cfg["context_color"], "__THIRD_LABEL__": cfg["context_label"],
        "__THIRD_AXIS__": third_axis, "__THIRD_UNIT__": "bp" if unit == "bp" else "%",
        "__PRICE_AXIS_TITLE__": f"{PRIMARY_LABEL} / price change since start (%)" if unit != "bp" else f"{PRIMARY_LABEL} change since start (%)",
    }.items():
        px_script = px_script.replace(token, value)
    px_canvas_id = f"chart-px-{abs(hash(bucket)) % 100000}"

    return f"""
    <section class="panel slide{' active' if active else ''}" data-bucket="{bucket}" style="--accent: {color}">
      <div class="panel-head">
        <h2>{bucket}</h2>
        <span class="timestamp">as of {latest['timestamp_utc']}</span>
      </div>

      <div class="panel-body">
        <div class="panel-left">
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

          <div class="headlines">
            <span class="label">Top risk headlines this run</span>
            <ul>{risk_headlines_html}</ul>
          </div>

          <div class="headlines">
            <span class="label">Top de-escalation headlines this run</span>
            <ul>{deescalation_headlines_html}</ul>
          </div>
        </div>

        <div class="panel-right">
          <span class="label chart-label">Composite score, last {len(chart_rows)} runs (~4 days)</span>
          <canvas class="trend-chart" id="{canvas_id}" height="190"></canvas>
          <script>
            new Chart(document.getElementById("{canvas_id}"), {{
              type: "line",
              data: {{
                labels: {chart_labels},
                datasets: [
                  {{
                    data: {chart_values},
                    borderColor: "{color}",
                    borderWidth: 1.5,
                    pointRadius: 2,
                    pointBackgroundColor: "{color}",
                    tension: 0.15,
                    fill: false,
                  }},
                  {{
                    data: {zero_line},
                    borderColor: "#8a8f9c",
                    borderWidth: 1,
                    borderDash: [4, 4],
                    pointRadius: 0,
                    fill: false,
                  }},
                ]
              }},
              options: {{
                responsive: true,
                plugins: {{ legend: {{ display: false }}, tooltip: {{ enabled: true, mode: "index", intersect: false }} }},
                scales: {{
                  x: {{
                    display: true,
                    ticks: {{ color: "#8a8f9c", maxTicksLimit: 6, font: {{ family: "IBM Plex Mono", size: 10 }} }},
                    grid: {{ color: "#2a2e3a" }},
                  }},
                  y: {{
                    display: true,
                    ticks: {{ color: "#8a8f9c", font: {{ family: "IBM Plex Mono", size: 10 }} }},
                    grid: {{ color: "#2a2e3a" }},
                  }},
                }},
              }}
            }});
          </script>

          <span class="label chart-label">Daily composite score, last {len(daily_rows)} days</span>
          <canvas class="trend-chart" id="{daily_canvas_id}" height="190"></canvas>
          <script>
            new Chart(document.getElementById("{daily_canvas_id}"), {{
              type: "line",
              data: {{
                labels: {daily_labels},
                datasets: [
                  {{
                    data: {daily_values},
                    borderColor: "{color}",
                    borderWidth: 1.5,
                    pointRadius: 2,
                    pointBackgroundColor: "{color}",
                    tension: 0.15,
                    fill: false,
                  }},
                  {{
                    data: {daily_zero_line},
                    borderColor: "#8a8f9c",
                    borderWidth: 1,
                    borderDash: [4, 4],
                    pointRadius: 0,
                    fill: false,
                  }},
                ]
              }},
              options: {{
                responsive: true,
                plugins: {{ legend: {{ display: false }}, tooltip: {{ enabled: true, mode: "index", intersect: false }} }},
                scales: {{
                  x: {{
                    display: true,
                    ticks: {{ color: "#8a8f9c", maxTicksLimit: 6, font: {{ family: "IBM Plex Mono", size: 10 }} }},
                    grid: {{ color: "#2a2e3a" }},
                  }},
                  y: {{
                    display: true,
                    ticks: {{ color: "#8a8f9c", font: {{ family: "IBM Plex Mono", size: 10 }} }},
                    grid: {{ color: "#2a2e3a" }},
                  }},
                }},
              }}
            }});
          </script>

        </div>
      </div>

      <div class="px-full">
        {signal_html}
        <span class="label chart-label">{px_title}</span>
        <div class="px-wrap"><canvas class="px-canvas" id="{px_canvas_id}"></canvas></div>
        <script>{px_script}</script>
      </div>
    </section>
    """


def _grouped_daily_history_html(daily_df: pd.DataFrame, buckets: list) -> str:
    """Nested <details> dropdowns: Quarter > Week, most recent first, with the
    latest date in each week on top. Plain HTML/CSS collapsibles -- no JS
    needed, consistent with the rest of this self-contained page."""
    if daily_df.empty:
        return "<p class=\"muted\">No daily rollup yet.</p>"

    df = daily_df.copy()
    df["date_dt"] = pd.to_datetime(df["date"])
    header_cells = "".join(f"<th>{b}</th>" for b in buckets)

    unique_dates = sorted(df["date_dt"].unique(), reverse=True)  # latest first

    quarters: "OrderedDict[str, OrderedDict[str, list]]" = OrderedDict()
    for d in unique_dates:
        d_ts = pd.Timestamp(d)
        quarter_label = f"{d_ts.year} Q{((d_ts.month - 1) // 3) + 1}"
        week_start = d_ts - pd.Timedelta(days=d_ts.weekday())  # Monday
        week_end = week_start + pd.Timedelta(days=6)
        week_label = f"Week of {week_start:%b %d} \u2013 {week_end:%b %d}"
        quarters.setdefault(quarter_label, OrderedDict()).setdefault(week_label, []).append(d_ts)

    quarter_blocks = []
    for qi, (quarter_label, weeks) in enumerate(quarters.items()):
        week_blocks = []
        for wi, (week_label, dates) in enumerate(weeks.items()):
            row_html = []
            for d_ts in dates:  # already latest-first
                date_str = d_ts.strftime("%Y-%m-%d")
                cells = []
                for bucket in buckets:
                    match = df[(df["date_dt"] == d_ts) & (df["bucket"] == bucket)]
                    if match.empty:
                        cells.append("<td class=\"muted\">\u2014</td>")
                        continue
                    r = match.iloc[0]
                    cells.append(
                        f"<td>{_fmt_score(r['composite_avg'])} "
                        f"<span class=\"muted\">({_fmt_pct(r['composite_pct_change_vs_prev_day'])})</span></td>"
                    )
                row_html.append(f"<tr><td>{date_str}</td>{''.join(cells)}</tr>")

            week_open = " open" if (qi == 0 and wi == 0) else ""
            week_blocks.append(f"""
            <details class="week-group"{week_open}>
              <summary>{week_label}</summary>
              <table class="daily-table">
                <thead><tr><th>Date</th>{header_cells}</tr></thead>
                <tbody>{''.join(row_html)}</tbody>
              </table>
            </details>
            """)

        quarter_open = " open" if qi == 0 else ""
        quarter_blocks.append(f"""
        <details class="quarter-group"{quarter_open}>
          <summary>{quarter_label}</summary>
          {''.join(week_blocks)}
        </details>
        """)

    return "\n".join(quarter_blocks) + """
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
    prices = {}
    if not daily_df.empty and len(daily_df):
        all_dates = daily_df["date"].astype(str).sort_values()
        tickers = {PRIMARY_TICKER} | {c["context_ticker"] for c in PRICE_SIGNALS.values()}
        prices = {t: _fetch_daily_closes(t, all_dates.iloc[0], all_dates.iloc[-1]) for t in sorted(tickers)}

    panel_list = []  # (bucket, html) -- skip buckets with no rows so tabs and slides always match
    for b in BUCKET_COLORS:
        html_b = _bucket_panel_html(b, log_df, daily_df, prices, active=not panel_list)
        if html_b:
            panel_list.append((b, html_b))
    panels_html = "\n".join(h for _, h in panel_list)
    tabs_html = "\n".join(
        f'<button class="tab{" active" if i == 0 else ""}" data-bucket="{b}" style="--accent: {BUCKET_COLORS[b]}">{b}</button>'
        for i, (b, _) in enumerate(panel_list)
    )
    daily_html = _grouped_daily_history_html(daily_df, list(BUCKET_COLORS.keys()))

    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Macro & Geopolitical Risk Sentiment</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<!-- Vendored in vendor/chart.umd.js rather than loaded from a CDN -- a CDN
     (cdnjs.cloudflare.com) was silently blocked on a real network, with no
     visible error to the person just looking at the page: the chart area
     just stayed blank. Self-hosting removes that failure mode entirely. -->
<script src="vendor/chart.umd.js"></script>
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
    max-width: 1280px;
    margin: 0 auto;
    border-bottom: 1px solid var(--rule);
  }}
  header h1 {{
    font-size: 1.4rem;
    font-weight: 600;
    margin: 0 0 0.3rem;
  }}
  header p {{ color: var(--muted); margin: 0; font-size: 0.9rem; }}
  main {{ max-width: 1280px; margin: 0 auto; padding: 1.5rem; }}
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
  .panel-body {{ display: flex; gap: 1.75rem; align-items: flex-start; flex-wrap: wrap; margin-top: 0.75rem; }}
  .panel-left {{ flex: 1 1 280px; min-width: 260px; }}
  .panel-right {{ flex: 1.4 1 460px; min-width: 380px; display: flex; flex-direction: column; }}
  .score-row {{ display: flex; align-items: baseline; gap: 0.75rem; margin: 0 0 0.3rem; }}
  .score {{ font-family: "IBM Plex Mono", ui-monospace, monospace; font-size: 2.1rem; font-weight: 600; }}
  .delta {{ color: var(--muted); font-size: 0.85rem; }}
  .breakdown {{ display: flex; flex-direction: column; gap: 0.25rem; margin-bottom: 0.75rem; font-size: 0.88rem; }}
  .breakdown .label {{ color: var(--muted); margin-right: 0.5rem; }}
  .breakdown .value {{ font-family: "IBM Plex Mono", ui-monospace, monospace; }}
  .chart-label {{ display: block; margin-top: 0.5rem; }}
  .trend-chart {{ width: 100%; max-height: 220px; margin: 0.4rem 0 1.25rem; }}
  .headlines {{ margin-top: 1rem; }}
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
  details.quarter-group {{ border-top: 1px solid var(--rule); padding: 0.6rem 0; }}
  details.quarter-group summary {{
    cursor: pointer; font-weight: 600; font-size: 0.95rem; list-style: none;
    display: flex; align-items: center; gap: 0.4rem;
  }}
  details.quarter-group summary::-webkit-details-marker {{ display: none; }}
  details.quarter-group summary::before {{ content: "▸"; color: var(--muted); font-size: 0.75rem; }}
  details.quarter-group[open] > summary::before {{ content: "▾"; }}
  details.week-group {{ margin: 0.5rem 0 0.5rem 1.25rem; }}
  details.week-group summary {{
    cursor: pointer; color: var(--muted); font-size: 0.85rem; list-style: none;
    display: flex; align-items: center; gap: 0.4rem; margin-bottom: 0.3rem;
  }}
  details.week-group summary::-webkit-details-marker {{ display: none; }}
  details.week-group summary::before {{ content: "▸"; font-size: 0.7rem; }}
  details.week-group[open] > summary::before {{ content: "▾"; }}
  nav.tabs {{ max-width: 1280px; margin: 0 auto; padding: 1rem 1.5rem 0; display: flex; gap: 0.5rem; flex-wrap: wrap; }}
  .tab {{
    background: var(--panel); color: var(--muted); border: 1px solid var(--rule);
    border-bottom: 2px solid transparent; padding: 0.55rem 1rem; cursor: pointer;
    font-family: "IBM Plex Sans", sans-serif; font-size: 0.9rem; font-weight: 500;
  }}
  .tab:hover {{ color: var(--text); }}
  .tab.active {{ color: var(--text); border-bottom-color: var(--accent); }}
  .slide {{ display: none; }}
  .slide.active {{ display: block; }}
  .px-full {{ margin-top: 1.25rem; padding-top: 1rem; border-top: 1px solid var(--rule); }}
  .px-wrap {{ position: relative; height: 540px; margin: 0.4rem 0 0.25rem; }}
  .signal {{ border: 1px solid var(--rule); border-left-width: 3px; padding: 0.6rem 0.8rem; margin: 0.5rem 0 0.6rem; font-size: 0.82rem; }}
  .signal-head {{ display: flex; align-items: baseline; gap: 0.75rem; flex-wrap: wrap; margin-bottom: 0.35rem; }}
  .signal .badge {{ font-weight: 600; font-size: 0.9rem; }}
  .signal-body div {{ margin-bottom: 0.15rem; }}
  .signal-confirming {{ border-left-color: #5FA777; }}
  .signal-confirming .badge {{ color: #5FA777; }}
  .signal-diverging {{ border-left-color: #D9822B; }}
  .signal-diverging .badge {{ color: #D9822B; }}
  .signal-quiet {{ border-left-color: var(--muted); }}
  .signal-quiet .badge {{ color: var(--muted); }}
  footer {{ max-width: 1280px; margin: 0 auto; padding: 1rem 1.5rem 2.5rem; color: var(--muted); font-size: 0.78rem; }}
</style>
</head>
<body>
<header>
  <h1>Macro &amp; geopolitical risk sentiment</h1>
  <p>Last scan: {last_updated} UTC · Positive score = escalating pressure · Negative = de-escalating</p>
</header>
<nav class="tabs">
  {tabs_html}
</nav>
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
<script>
  // Slide switcher. Charts built while a slide is display:none can come up at the
  // wrong size, so each chart in the newly shown slide gets an explicit resize().
  (function () {{
    var tabs = document.querySelectorAll(".tab");
    var slides = document.querySelectorAll(".slide");
    tabs.forEach(function (tab) {{
      tab.addEventListener("click", function () {{
        var bucket = tab.getAttribute("data-bucket");
        tabs.forEach(function (t) {{ t.classList.toggle("active", t === tab); }});
        slides.forEach(function (s) {{
          var on = s.getAttribute("data-bucket") === bucket;
          s.classList.toggle("active", on);
          if (on) {{
            s.querySelectorAll("canvas").forEach(function (c) {{
              var ch = window.Chart && Chart.getChart ? Chart.getChart(c) : null;
              if (ch) ch.resize();
            }});
          }}
        }});
      }});
    }});
  }})();
</script>
</body>
</html>
"""
    OUTPUT_PATH.write_text(html, encoding="utf-8")
    print(f"Dashboard written -> {OUTPUT_PATH}")


if __name__ == "__main__":
    build_dashboard()
