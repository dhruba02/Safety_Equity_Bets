"""
app.py — Safe Equity Bets
Streamlit UI for the equity screening pipeline.
"""

import os
import streamlit as st
import pandas as pd
from pipeline import run_pipeline, load_latest

# ── Page config ────────────────────────────────────────────
st.set_page_config(
    page_title="Safe Equity Bets",
    page_icon="📈",
    layout="wide"
)

# ── Password gate ──────────────────────────────────────────
def check_password():
    app_password = os.environ.get("APP_PASSWORD", "")
    if not app_password:
        return True  # No password set — allow access (local dev)

    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if st.session_state.authenticated:
        return True

    st.title("📈 Safe Equity Bets")
    st.markdown("Please enter the access password to continue.")
    pwd = st.text_input("Password", type="password")
    if st.button("Login"):
        if pwd == app_password:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    return False

if not check_password():
    st.stop()

# ── Styling ────────────────────────────────────────────────
st.markdown("""
<style>
    .block-container { padding-top: 2rem; }
    .stDataFrame { font-size: 13px; }
    div[data-testid="metric-container"] {
        background: #f8f9fa;
        border: 1px solid #e0e0e0;
        border-radius: 8px;
        padding: 12px 16px;
    }
    .commentary-cell { font-size: 12px; color: #444; }
</style>
""", unsafe_allow_html=True)


# ── Commentary generator ───────────────────────────────────
def generate_commentary(row):
    """Rule-based single-line commentary explaining why the stock stood out."""
    notes = []

    pm = row.get('profitMargins', None)
    if pm is not None and pm > 0.20:
        notes.append(f"strong margins ({pm*100:.0f}%)")

    eps = row.get('eps_growth', None)
    if eps is not None and eps > 0.15:
        notes.append(f"solid EPS growth ({eps*100:.0f}%)")

    upside = row.get('pct_upside', None)
    if upside is not None and upside > 0.15:
        notes.append(f"analyst upside {upside*100:.0f}%")

    rec = row.get('recommendationMean', None)
    if rec is not None and rec <= 2.0:
        notes.append("analyst consensus Buy")

    de = row.get('debtToEquity', None)
    if de is not None and de < 20:
        notes.append("low debt")

    pos = row.get('52w_position_score', None)
    if pos is not None and pos < 0.40:
        notes.append("trading near 52w low")
    elif pos is not None and pos > 0.75:
        notes.append("strong price momentum")

    if not notes:
        return "Passes all 5 quality filters"
    return "• " + "  •  ".join(notes)


# ── Display columns and labels ─────────────────────────────
DISPLAY_COLS = {
    'symbol':             'Symbol',
    'longName':           'Company',
    'sector':             'Sector',
    'currentPrice':       'Price (₹)',
    'trailingPE':         'P/E',
    'forwardPE':          'Fwd P/E',
    'priceToBook':        'P/B',
    'profitMargins':      'Margin %',
    'revenueGrowth':      'Rev Growth %',
    'earningsGrowth':     'Earn Growth %',
    'eps_growth':         'EPS Growth %',
    'debtToEquity':       'D/E',
    'pct_upside':         'Analyst Upside %',
    'recommendationMean': 'Analyst Score',
    '52w_position_score': '52w Position',
    'sector_tag':         'Note',
    'commentary':         'Why it stood out',
}


def format_df(df):
    """Format dataframe for display."""
    df = df.copy()

    # Add commentary
    df['commentary'] = df.apply(generate_commentary, axis=1)

    # Keep only available display cols
    cols = [c for c in DISPLAY_COLS if c in df.columns]
    df = df[cols].rename(columns=DISPLAY_COLS)

    # Format percentages
    for col_key, col_label in DISPLAY_COLS.items():
        if col_key in ['profitMargins', 'revenueGrowth', 'earningsGrowth',
                       'eps_growth', 'pct_upside']:
            if col_label in df.columns:
                df[col_label] = (df[col_label] * 100).round(1)

    # Round numeric cols
    for col in ['P/E', 'Fwd P/E', 'P/B', 'D/E', '52w Position', 'Analyst Score']:
        if col in df.columns:
            df[col] = df[col].round(2)

    return df.sort_values('Sector').reset_index(drop=True)


# ── Session state init ─────────────────────────────────────
if 'df' not in st.session_state:
    st.session_state.df = None
if 'snapshot_date' not in st.session_state:
    st.session_state.snapshot_date = None
if 'pipeline_running' not in st.session_state:
    st.session_state.pipeline_running = False


# ── Header ─────────────────────────────────────────────────
st.title("📈 Safe Equity Bets")
st.caption("Nifty 500 · Fundamentally screened · Multi-layer quality filter")

st.divider()

# ── Controls row ───────────────────────────────────────────
col_btn, col_info, col_load = st.columns([1, 3, 1])

with col_btn:
    run_clicked = st.button(
        "▶  Run Pipeline",
        type="primary",
        disabled=st.session_state.pipeline_running,
        use_container_width=True
    )

with col_load:
    load_clicked = st.button(
        "⟳  Load Latest",
        use_container_width=True
    )

with col_info:
    if st.session_state.snapshot_date:
        st.info(f"Showing results for snapshot: **{st.session_state.snapshot_date}**")
    else:
        st.info("Click **Load Latest** to see existing results, or **Run Pipeline** to fetch fresh data.")


# ── Load latest on button click ────────────────────────────
if load_clicked:
    with st.spinner("Loading latest results from Azure Delta Lake..."):
        try:
            df, date = load_latest()
            st.session_state.df = df
            st.session_state.snapshot_date = date
            st.success(f"Loaded {len(df)} stocks from snapshot {date}")
        except Exception as e:
            st.error(f"Failed to load data: {e}")


# ── Run pipeline on button click ───────────────────────────
if run_clicked:
    st.session_state.pipeline_running = True
    log_container = st.empty()
    log_lines = []

    def stream_log(msg):
        log_lines.append(msg)
        log_container.code("\n".join(log_lines), language=None)

    try:
        df, date = run_pipeline(log=stream_log)
        st.session_state.df = df
        st.session_state.snapshot_date = date
        st.success(f"✅ Pipeline complete — {len(df)} stocks screened for {date}")
    except Exception as e:
        st.error(f"Pipeline failed: {e}")
    finally:
        st.session_state.pipeline_running = False


# ── Results display ────────────────────────────────────────
if st.session_state.df is not None:
    df = st.session_state.df

    st.divider()

    # Summary metrics
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Stocks in Universe", len(df))
    m2.metric("Sectors Represented", df['sector'].nunique() if 'sector' in df.columns else "—")

    avg_upside = df['pct_upside'].mean() * 100 if 'pct_upside' in df.columns else None
    m3.metric("Avg Analyst Upside", f"{avg_upside:.1f}%" if avg_upside else "—")

    avg_margin = df['profitMargins'].mean() * 100 if 'profitMargins' in df.columns else None
    m4.metric("Avg Profit Margin", f"{avg_margin:.1f}%" if avg_margin else "—")

    st.divider()

    # Sector filter
    sectors = ["All"] + sorted(df['sector'].dropna().unique().tolist()) if 'sector' in df.columns else ["All"]
    selected_sector = st.selectbox("Filter by sector", sectors)

    df_view = df if selected_sector == "All" else df[df['sector'] == selected_sector]
    df_display = format_df(df_view)

    st.dataframe(
        df_display,
        use_container_width=True,
        height=600,
        hide_index=True,
    )

    st.caption(f"{len(df_view)} stocks shown · Filtered from Nifty 500 using 5-metric percentile screen (20th–85th percentile)")
