# Safe Equity Bets 📈

A stock screening application for Indian equity markets that identifies fundamentally sound stocks from the Nifty 500 universe using a multi-layer quality filter built on Azure.

## What it does

- Screens all 501 Nifty 500 stocks using Yahoo Finance fundamental data
- Applies a strict 5-metric percentile filter (20th–85th percentile simultaneously) to surface ~47 high-quality names
- Stores data in a three-layer Delta Lake on Azure Data Lake Storage Gen2
- Serves results through a password-protected Streamlit web application deployed on Azure Container Apps

## Filter Criteria

A stock must simultaneously pass all five metrics to be included:

| Metric | What it filters |
|--------|----------------|
| `profitMargins` | Removes unprofitable or margin-thin businesses |
| `eps_growth` | Removes declining or stagnant earnings |
| `trailingPE` | Removes distressed (very cheap) and speculative (very expensive) stocks |
| `pct_upside` | Removes stocks with no analyst conviction |
| `debtToEquity` | Removes overleveraged companies |

## Architecture

```
yfinance API
     │
     ▼
Raw Layer (Delta Lake)          ← 33 columns, all Nifty 500 stocks
     │
     ▼
Enriched Layer (Delta Lake)     ← + 5 derived metrics
     │
     ▼
Cleaned Layer (Delta Lake)      ← ~47 filtered stocks, null-filled
     │
     ▼
Streamlit UI (Azure Container Apps)
```

## Azure Infrastructure

| Resource | Purpose |
|----------|---------|
| ADLS Gen2 | Three-layer Delta Lake storage |
| Azure Key Vault | Secrets management |
| Azure Container Registry | Docker image storage |
| Azure Container Apps | Serverless app hosting |
| Azure Service Principal | Application identity & auth |

## Tech Stack

- **Python 3.12** — pipeline logic
- **yfinance + niftystocks** — data source
- **delta-rs (deltalake)** — Delta Lake read/write
- **Streamlit** — web UI
- **Docker** — containerisation
- **Azure** — cloud infrastructure

## Project Structure

```
├── app.py              # Streamlit web application
├── pipeline.py         # Core pipeline logic
├── requirements.txt    # Python dependencies
├── Dockerfile          # Container build
└── README.md
```

## Running Locally

```bash
# Install dependencies
pip install -r requirements.txt

# Set Azure credentials as environment variables
setx AZURE_TENANT_ID "your-tenant-id"
setx AZURE_CLIENT_ID "your-client-id"
setx AZURE_CLIENT_SECRET "your-client-secret"

# Run the app
streamlit run app.py
```

## Known Limitations

- Banking and Financial Services stocks are structurally excluded by the debt/equity filter — a separate pipeline with sector-appropriate metrics is needed
- ~50 Nifty 500 tickers fail on yfinance due to stale symbol mappings
- All Azure resources are deployed in East US — Central India would be more appropriate for production
- Pipeline run from the UI takes 5–8 minutes (full yfinance fetch for 500 stocks)

## Author

Dhruba Jyoti — [github.com/dhruba02](https://github.com/dhruba02)
