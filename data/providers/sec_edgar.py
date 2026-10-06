# -*- coding: utf-8 -*-
"""SEC EDGAR XBRL company facts: free, and point-in-time through each fact's filing date."""
import time

import pandas as pd

from data.providers.base import ProviderError, get_credential, http_get

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
# SEC fair-access policy allows 10 requests per second
REQUEST_INTERVAL = 0.12
FACT_COLUMNS = ["concept", "unit", "period_start", "period_end", "value",
                "fiscal_year", "fiscal_period", "form", "filed", "accession"]


def get_headers() -> dict:
    agent = get_credential("SEC_USER_AGENT")
    if not agent:
        raise ProviderError('SEC_USER_AGENT is not set; SEC requires "Your Name your@email.com"')
    return {"User-Agent": agent, "Accept-Encoding": "gzip, deflate"}


def get_ticker_ciks() -> dict:
    """Current SEC ticker → CIK map (tickers use '-' for share classes, e.g. BRK-B)."""
    rows = http_get(TICKERS_URL, headers=get_headers()).json().values()
    return {row["ticker"].upper(): int(row["cik_str"]) for row in rows}


def get_company_facts(cik: int) -> dict:
    time.sleep(REQUEST_INTERVAL)
    return http_get(FACTS_URL.format(cik=cik), headers=get_headers()).json()


def parse_company_facts(payload: dict, concepts) -> pd.DataFrame:
    """Facts for "taxonomy:Concept" names; instant facts get an empty period_start."""
    rows = []
    for qualified in concepts:
        taxonomy, concept = qualified.split(":", 1)
        node = (payload.get("facts") or {}).get(taxonomy, {}).get(concept)
        for unit, facts in ((node or {}).get("units") or {}).items():
            rows.extend({
                "concept": qualified, "unit": unit,
                "period_start": fact.get("start", ""), "period_end": fact["end"],
                "value": fact["val"], "fiscal_year": fact.get("fy"),
                "fiscal_period": fact.get("fp"), "form": fact.get("form"),
                "filed": fact["filed"], "accession": fact["accn"],
            } for fact in facts)
    return pd.DataFrame(rows, columns=FACT_COLUMNS)
