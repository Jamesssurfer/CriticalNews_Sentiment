"""Bucket + query configuration for the news sentiment scanner.

Edit the query lists here to tune what each bucket tracks. Each bucket has two
query groups:

  - risk_queries: escalation / negative-event queries. Drives the "pressure" side.
  - deescalation_queries: calming / resolution queries. Drives the offsetting side.

Why both sides exist: risk_queries are inherently negative-coded (they describe
strikes, interventions, tariffs). If a conflict cools off, risk_queries simply
return fewer articles -- that reads as "quiet," not "improving." deescalation_queries
give the scanner a way to detect and score actual de-escalation (ceasefires, rate
cuts, tariff rollbacks) instead of just an absence of bad news. See composite score
logic in scanner.py.

Each bucket also picks which scoring model drives its composite score, via
"sentiment_model": "finbert" or "vader". Both models are still run and logged for
every bucket (see sentiment_engine.py) -- this only controls which number the
composite formula and the top-headlines ranking actually use.

Default choice, based on checking real output from both models on the same
headlines: FinBERT is trained on financial-phrasebank / analyst-report language,
where words like "seize," "advance," "inject," "strengthen" are business-positive.
On militarized/policy language (port seizures, strikes, tariff retaliation) that
reads exactly backwards -- confirmed in this repo's first live run, where
Middle East Oil and Trade Wars both showed strongly *positive* FinBERT tone on
headlines about an active port seizure and tariff escalation. VADER, a cruder
general-purpose lexicon, got the sign right on those same headlines. Macro
Interventions is the one bucket where FinBERT's training domain is actually close
to the subject matter (central-bank / Treasury language resembles analyst-report
language), so it stays on FinBERT by default.
"""

# Articles pulled per individual query, per run. 3 buckets x ~10 queries x this
# number = total articles fetched (and content-extracted) each 4-hour cycle.
# Keep this modest -- each article triggers an HTTP fetch + trafilatura extraction
# + two model scoring passes.
MAX_ARTICLES_PER_QUERY = 8

BUCKETS = {
    "middle_east_oil": {
        "label": "Middle East Oil Risk",
        "sentiment_model": "vader",
        "risk_queries": [
            "Israel Iran strike",
            "Houthi Red Sea attack",
            "Strait of Hormuz tension",
            "Saudi Arabia oil facility attack",
            "Israel Hezbollah Lebanon escalation",
            "US military strike Iran",
        ],
        "deescalation_queries": [
            "Israel Iran ceasefire",
            "Gaza ceasefire deal",
            "Iran nuclear deal talks",
            "Middle East peace talks",
        ],
    },
    "macro_interventions": {
        "label": "Macroeconomic Interventions",
        "sentiment_model": "finbert",
        "risk_queries": [
            "Bank of Japan yen intervention",
            "Federal Reserve emergency action",
            "US Treasury yield spike",
            "quantitative tightening surprise",
            "central bank liquidity injection",
        ],
        "deescalation_queries": [
            "Federal Reserve rate cut",
            "Bank of Japan holds policy steady",
            "bond market stabilizes",
            "Treasury yields fall",
        ],
    },
    "trade_wars": {
        "label": "Trade Wars & Escalations",
        "sentiment_model": "vader",
        "risk_queries": [
            "US Canada tariff dispute",
            "China tariff retaliation",
            "trade war escalation",
            "export controls China US",
            "supply chain border restriction",
        ],
        "deescalation_queries": [
            "tariff rollback agreement",
            "US China trade deal",
            "trade truce",
            "tariff exemption agreement",
        ],
    },
}


# ---------------------------------------------------------------------------
# Price-vs-sentiment status (read only by dashboard.py; scanner.py ignores it)
# ---------------------------------------------------------------------------
# Every bucket's daily composite is compared against DXY. The "context" series
# is drawn as a third line; it never feeds the Confirming/Diverging status
# (Macro's 10Y yield only feeds the regime label).
#
# expected_sign: +1 = DXY expected to move WITH the composite, -1 = AGAINST it.
# Keys must match the bucket "label" strings above exactly.
PRIMARY_TICKER = "DX-Y.NYB"
PRIMARY_LABEL = "DXY"
PRIMARY_COLOR = "#E7E5DE"

SIGNAL_WINDOW_DAYS = 5     # compare 5-trading-day changes
SIGNAL_SIGMA = 1.0         # "beyond normal" = more than this many std devs of the series' own 5-day changes
SIGNAL_MIN_DAYS = 20       # aligned trading days needed before any status can fire
CORR_MIN_DAYS = 60         # aligned trading days needed before a realized correlation is shown

# Flag a 1-day jump bigger than this % on a continuous futures ticker in the
# tooltip -- Yahoo's GC=F / BZ=F are not back-adjusted, so a contract roll can
# look like a real move.
ROLL_JUMP_PCT = {"BZ=F": 4.0, "GC=F": 2.5}

PRICE_SIGNALS = {
    "Middle East Oil Risk": {
        "expected_sign": +1,
        "context_ticker": "BZ=F",
        "context_label": "Brent (context)",
        "context_unit": "%",
        "context_color": "#7FB069",
    },
    "Macroeconomic Interventions": {
        "expected_sign": -1,
        "context_ticker": "^TNX",
        "context_label": "10Y yield change (regime)",
        "context_unit": "bp",
        "context_color": "#B48EAD",
        "regime": True,
    },
    "Trade Wars & Escalations": {
        "expected_sign": +1,
        "context_ticker": "GC=F",
        "context_label": "Gold (context)",
        "context_unit": "%",
        "context_color": "#7FC8C0",
    },
}
