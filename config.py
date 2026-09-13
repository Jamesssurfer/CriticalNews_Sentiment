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
"""

# Articles pulled per individual query, per run. 3 buckets x ~10 queries x this
# number = total articles fetched (and content-extracted) each 4-hour cycle.
# Keep this modest -- each article triggers an HTTP fetch + trafilatura extraction
# + two model scoring passes.
MAX_ARTICLES_PER_QUERY = 8

BUCKETS = {
    "middle_east_oil": {
        "label": "Middle East Oil Risk",
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
