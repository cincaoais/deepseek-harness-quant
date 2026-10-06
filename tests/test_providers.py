import numpy as np
import pandas as pd

from data.providers import eodhd, fred, nasdaqtrader, sec_edgar, yahoo


def test_yahoo_parse_download_skips_symbols_without_data():
    index = pd.to_datetime(["2026-09-01", "2026-09-02"])
    columns = pd.MultiIndex.from_product([["AAPL", "GONE"], list(yahoo.COLUMNS)])
    aapl = [[1, 2, 0.5, 1.5, 1.4, 100], [1.5, 2.5, 1, 2, 1.9, 200]]
    raw = pd.DataFrame(np.hstack([aapl, np.full((2, 6), np.nan)]), index=index, columns=columns)
    frame = yahoo.parse_download(raw, "AAPL")
    assert frame.to_dict("list") == {
        "date": ["2026-09-01", "2026-09-02"], "open": [1, 1.5], "high": [2, 2.5], "low": [0.5, 1],
        "close": [1.5, 2], "adj_close": [1.4, 1.9], "volume": [100, 200]}
    assert yahoo.parse_download(raw, "GONE") is None


def test_eodhd_prices_are_rescaled_across_splits():
    rows = [
        {"date": "2020-08-28", "open": 504.05, "high": 505.77, "low": 498.31, "close": 499.23,
         "adjusted_close": 120.9557, "volume": 187630000},
        {"date": "2020-08-31", "open": 127.58, "high": 131.0, "low": 126.0, "close": 129.04,
         "adjusted_close": 125.0575, "volume": 225702700},
    ]
    frame = eodhd.parse_eod(rows, [{"date": "2020-08-31", "split": "4.000000/1.000000"}])
    assert frame["close"].round(4).tolist() == [124.8075, 129.04]
    assert frame["volume"].tolist() == [187630000, 225702700]


def test_nasdaqtrader_listing_drops_test_issues_preferreds_and_footer():
    text = (
        "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\n"
        "BRK.B|Berkshire Hathaway Inc. New Common Stock|N|BRK.B|N|100|N|BRK.B\n"
        "SPY|SPDR S&P 500 ETF Trust|P|SPY|Y|100|N|SPY\n"
        "ABR$D|Arbor Realty Preferred|N|ABRpD|N|100|N|ABR-D\n"
        "ZXIET|Test Issue|N|ZXIET|N|100|Y|ZXIET\n"
        "File Creation Time: 1006202603:03|||||||\n"
    )
    frame = nasdaqtrader.parse_listing(text, "otherlisted")
    assert frame[["code", "exchange", "is_etf"]].values.tolist() == [
        ["BRK.B", "NYSE", False], ["SPY", "NYSE Arca", True]]


def test_fred_csv_drops_missing_observations():
    frame = fred.parse_series_csv("observation_date,DGS10\n1962-01-02,4.06\n1962-02-12,\n1962-02-13,.\n")
    assert frame.values.tolist() == [["1962-01-02", 4.06]]


def test_sec_facts_keep_filing_dates_and_mark_instants():
    payload = {"facts": {"dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
        {"end": "2026-07-17", "val": 14594180000, "accn": "a2", "fy": 2026, "fp": "Q3",
         "form": "10-Q", "filed": "2026-07-31"}]}}}}}
    frame = sec_edgar.parse_company_facts(
        payload, ["dei:EntityCommonStockSharesOutstanding", "us-gaap:NetIncomeLoss"])
    assert frame[["concept", "period_start", "period_end", "value", "filed"]].values.tolist() == [
        ["dei:EntityCommonStockSharesOutstanding", "", "2026-07-17", 14594180000, "2026-07-31"]]
