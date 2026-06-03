"""
pipeline.py — Safe Equity Bets
Core pipeline logic extracted from Equity_tracker notebook.
Called by app.py when user clicks "Run Pipeline".
"""

import os
import time
import subprocess
from datetime import datetime

import pandas as pd
import pyarrow
from azure.identity import EnvironmentCredential
from azure.keyvault.secrets import SecretClient
from deltalake import DeltaTable, write_deltalake

# ── Configuration ──────────────────────────────────────────
KEY_VAULT_URI   = "https://kv-my-equity-tracker.vault.azure.net"
STORAGE_ACCOUNT = "stmyequitytracker"
CONTAINER       = "equity-data"
RAW_PATH        = f"abfss://{CONTAINER}@{STORAGE_ACCOUNT}.dfs.core.windows.net/stocks/raw"
ENRICHED_PATH   = f"abfss://{CONTAINER}@{STORAGE_ACCOUNT}.dfs.core.windows.net/stocks/enriched"
CLEANED_PATH    = f"abfss://{CONTAINER}@{STORAGE_ACCOUNT}.dfs.core.windows.net/stocks/cleaned"

PERCENTILE_FILTER_METRICS = [
    'profitMargins', 'eps_growth', 'trailingPE', 'pct_upside', 'debtToEquity'
]
MIN_FILL_RATE = 0.70

RAW_METRICS = [
    'longName', 'sector',
    'currentPrice', 'fiftyTwoWeekHigh', 'fiftyTwoWeekLow',
    'revenueGrowth', 'earningsGrowth', 'earningsQuarterlyGrowth',
    '52WeekChange', 'trailingEps', 'forwardEps',
    'profitMargins', 'ebitdaMargins', 'operatingMargins', 'grossMargins',
    'debtToEquity', 'totalDebt', 'totalCash',
    'trailingPE', 'forwardPE', 'priceToBook',
    'priceToSalesTrailing12Months', 'enterpriseToRevenue', 'pegRatio',
    'recommendationMean', 'numberOfAnalystOpinions', 'targetMeanPrice',
    'volume', 'averageVolume', 'heldPercentInstitutions', 'beta'
]


def get_storage_options():
    """Authenticate via Service Principal and return Delta Lake storage options."""
    credential = EnvironmentCredential()
    kv_client = SecretClient(vault_url=KEY_VAULT_URI, credential=credential)
    connection_string = kv_client.get_secret("adls-connection-string").value
    account_key = connection_string.split("AccountKey=")[1].split(";")[0]
    return {"account_name": STORAGE_ACCOUNT, "account_key": account_key}


def run_raw_layer(storage_options, log):
    """Pull fresh data from yfinance → write to Raw Layer."""
    subprocess.run(["pip", "install", "niftystocks", "-q"], check=True)
    import yfinance as yf
    from niftystocks import ns

    tickers = ns.get_nifty500_with_ns()
    log(f"Stock universe loaded: {len(tickers)} stocks")

    snapshot_date = datetime.today().strftime('%Y-%m-%d')
    rows, failed = [], []

    log(f"Fetching data for {len(tickers)} stocks — this takes 5-8 minutes...")
    for i, ticker in enumerate(tickers, 1):
        try:
            info = yf.Ticker(ticker).info
            if info.get('currentPrice') is None:
                failed.append(ticker)
                continue
            row = {'symbol': ticker}
            row.update({m: info.get(m, None) for m in RAW_METRICS})
            row['snapshot_date'] = snapshot_date
            rows.append(row)
        except Exception:
            failed.append(ticker)

        if i % 50 == 0:
            log(f"  Processed {i}/{len(tickers)} stocks...")
        time.sleep(0.5)

    df_raw = pd.DataFrame(rows)
    write_deltalake(RAW_PATH, df_raw, mode="append", schema_mode="merge",
                    storage_options=storage_options)

    log(f"Raw layer written: {len(rows)} stocks fetched, {len(failed)} failed")
    return snapshot_date


def run_enriched_layer(storage_options, log):
    """Calculate derived metrics → write to Enriched Layer."""
    dt_raw = DeltaTable(RAW_PATH, storage_options=storage_options)
    df_all_raw = dt_raw.to_pandas()

    dt_enriched = DeltaTable(ENRICHED_PATH, storage_options=storage_options)
    df_all_enriched = dt_enriched.to_pandas()

    raw_dates = set(df_all_raw['snapshot_date'].unique())
    enriched_dates = set(df_all_enriched['snapshot_date'].unique())
    dates_to_process = raw_dates - enriched_dates

    if not dates_to_process:
        log("Enriched layer already up to date — nothing to process")
        return

    for date in sorted(dates_to_process):
        log(f"Enriching snapshot: {date}")
        df = df_all_raw[df_all_raw['snapshot_date'] == date].copy()

        df['eps_growth'] = (
            (df['forwardEps'] - df['trailingEps']) / df['trailingEps'].abs()
        )
        df['cash_coverage'] = df['totalCash'] / df['totalDebt']
        df['52w_position_score'] = (
            (df['currentPrice'] - df['fiftyTwoWeekLow']) /
            (df['fiftyTwoWeekHigh'] - df['fiftyTwoWeekLow'])
        )
        df['pct_upside'] = (
            (df['targetMeanPrice'] - df['currentPrice']) / df['currentPrice']
        )
        df['volume_surge'] = df['volume'] / df['averageVolume']

        write_deltalake(ENRICHED_PATH, df, mode="append", schema_mode="merge",
                        storage_options=storage_options)
        log(f"  Enriched layer written: {len(df)} stocks for {date}")


def run_cleaned_layer(storage_options, log):
    """Apply 3 cleaning rules → write to Cleaned Layer. Returns cleaned DataFrame."""
    dt_enriched = DeltaTable(ENRICHED_PATH, storage_options=storage_options)
    df_all_enriched = dt_enriched.to_pandas()

    try:
        dt_cleaned = DeltaTable(CLEANED_PATH, storage_options=storage_options)
        df_all_cleaned = dt_cleaned.to_pandas()
        cleaned_dates = set(df_all_cleaned['snapshot_date'].unique())
    except Exception:
        cleaned_dates = set()

    enriched_dates = set(df_all_enriched['snapshot_date'].unique())
    dates_to_clean = enriched_dates - cleaned_dates

    if not dates_to_clean:
        log("Cleaned layer already up to date — reading latest snapshot")
    else:
        for date in sorted(dates_to_clean):
            log(f"Cleaning snapshot: {date}")
            df = df_all_enriched[df_all_enriched['snapshot_date'] == date].copy()

            # Rule 1 — Percentile filter
            available = [m for m in PERCENTILE_FILTER_METRICS if m in df.columns]
            before = len(df)
            for metric in available:
                low = df[metric].quantile(0.20)
                high = df[metric].quantile(0.85)
                df = df[df[metric].between(low, high)]
            log(f"  Rule 1 (percentile filter): {len(df)} stocks kept ({before - len(df)} removed)")

            # Rule 2 — Drop sparse columns
            fill_rates = df.notna().mean()
            metadata_cols = ['symbol', 'longName', 'sector', 'snapshot_date']
            cols_to_drop = [
                c for c in fill_rates[fill_rates < MIN_FILL_RATE].index
                if c not in metadata_cols
            ]
            df = df.drop(columns=cols_to_drop)
            log(f"  Rule 2 (drop sparse cols): {len(df.columns)} columns remain")

            # Rule 3 — Sector tag + median fill
            financial_sectors = ['Financial Services', 'Banking', 'Insurance']
            df['sector_tag'] = df['sector'].apply(
                lambda x: 'Banking/Financial - needs separate treatment'
                if x in financial_sectors else ''
            ).astype(str)

            numeric_cols = df.select_dtypes(include='number').columns.tolist()
            if df['sector'].isna().all():
                for col in numeric_cols:
                    if df[col].isna().any():
                        df[col] = df[col].fillna(df[col].median())
            else:
                for col in numeric_cols:
                    if df[col].isna().any():
                        df[col] = df.groupby('sector')[col].transform(
                            lambda x: x.fillna(x.median())
                        )
                        df[col] = df[col].fillna(df[col].median())

            df = df.reset_index(drop=True)
            for col in df.columns:
                if df[col].dtype == object and df[col].isna().all():
                    df[col] = df[col].astype('float64')

            write_deltalake(CLEANED_PATH, df, mode="append", schema_mode="merge",
                            storage_options=storage_options)
            log(f"  Cleaned layer written: {len(df)} stocks for {date}")

    # Return latest cleaned snapshot
    dt_cleaned = DeltaTable(CLEANED_PATH, storage_options=storage_options)
    df_final = dt_cleaned.to_pandas()
    latest_date = df_final['snapshot_date'].max()
    return df_final[df_final['snapshot_date'] == latest_date].copy()


def run_pipeline(log=print):
    """
    Run the full pipeline end-to-end.
    log: callable that accepts a string — used to stream progress to the UI.
    Returns: (df_results, snapshot_date) tuple.
    """
    log("Connecting to Azure...")
    storage_options = get_storage_options()
    log("Connected to Azure Key Vault and storage.")

    log("--- Step 1/3: Raw data pull ---")
    snapshot_date = run_raw_layer(storage_options, log)

    log("--- Step 2/3: Enriched layer ---")
    run_enriched_layer(storage_options, log)

    log("--- Step 3/3: Cleaned layer ---")
    df = run_cleaned_layer(storage_options, log)

    log(f"Pipeline complete. {len(df)} stocks in filtered universe for {snapshot_date}.")
    return df, snapshot_date


def load_latest(log=print):
    """
    Load the latest cleaned snapshot without running the pipeline.
    Used on app startup to show last results immediately.
    """
    log("Loading latest results from Azure...")
    storage_options = get_storage_options()
    dt = DeltaTable(CLEANED_PATH, storage_options=storage_options)
    df = dt.to_pandas()
    latest_date = df['snapshot_date'].max()
    return df[df['snapshot_date'] == latest_date].copy(), latest_date
