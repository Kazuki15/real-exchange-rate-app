# Real Exchange Rate (RER) explorer built on IMF monthly CPI and exchange-rate data.
#
# Definitions
# -----------
# S(t)   = units of Country 2 currency per 1 unit of Country 1 currency
# RER(t) = S(t) * P1(t) / P2(t)
# RER*   = mean of RER(t) over valid months in the selected sample
# Real Index(t) = 100 * RER(t) / RER*
#
# Run:  streamlit run app.py
#
from __future__ import annotations

import io
import os
import re

import pandas as pd
import plotly.graph_objects as go
import pycountry
import requests
import streamlit as st
from plotly.subplots import make_subplots

BASE_URL = "https://api.imf.org/external/sdmx/3.0"
FIRST_MONTH = pd.Timestamp("1976-01-01")
EURO_AREA = "G163"
HEADERS_CSV = {"Accept": "application/vnd.sdmx.data+csv;version=2.0.0"}
HEADERS_JSON = {"Accept": "application/vnd.sdmx.data+json;version=2.0.0"}


# --------------------------------------------------------------------------
# Country list
# --------------------------------------------------------------------------
@st.cache_data
def country_options() -> dict[str, str]:
    # Label -> IMF code. ISO3 countries plus the Euro area (G163).
    opts = {f"{c.name} ({c.alpha_3})": c.alpha_3 for c in pycountry.countries}
    opts["Euro area (G163)"] = EURO_AREA
    opts["Kosovo (XKX)"] = "XKX"
    opts["West Bank and Gaza (WBG)"] = "WBG"
    return dict(sorted(opts.items()))


def short_name(label: str) -> str:
    return re.sub(r"\s*\([A-Z0-9]{3,4}\)$", "", label)


# --------------------------------------------------------------------------
# IMF data retrieval
# --------------------------------------------------------------------------
def _parse_period(p: str):
    # '1976-M01' or '1976-01' -> Timestamp (first of month); otherwise NaT.
    m = re.match(r"^(\d{4})-(?:M)?(\d{1,2})$", str(p).strip())
    if not m:
        return pd.NaT
    year, month = int(m.group(1)), int(m.group(2))
    if not 1 <= month <= 12:
        return pd.NaT
    return pd.Timestamp(year=year, month=month, day=1)


def _to_series(periods, values) -> pd.Series:
    idx = pd.DatetimeIndex([_parse_period(p) for p in periods])
    vals = pd.to_numeric(pd.Series(list(values)), errors="coerce").to_numpy()
    s = pd.Series(vals, index=idx)
    s = s[~s.index.isna()]
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s.dropna()


def _parse_csv(text: str) -> pd.Series:
    df = pd.read_csv(io.StringIO(text))
    if "TIME_PERIOD" not in df.columns or "OBS_VALUE" not in df.columns:
        return pd.Series(dtype=float)
    return _to_series(df["TIME_PERIOD"], df["OBS_VALUE"])


def _parse_json(obj: dict) -> pd.Series:
    data = obj.get("data", obj)
    datasets = data.get("dataSets") or []
    if not datasets:
        return pd.Series(dtype=float)
    structs = data.get("structures") or [data.get("structure")]
    dims = structs[0]["dimensions"]["observation"]
    time_ids = [v.get("id") or v.get("value") for v in dims[0]["values"]]
    periods, values = [], []
    for ser in datasets[0].get("series", {}).values():
        for k, v in ser.get("observations", {}).items():
            periods.append(time_ids[int(k)])
            values.append(v[0])
    return _to_series(periods, values)


# Dataflow versions known to work after the IMF's 2026 SDMX 3.0 refactor; "+" = latest.
FLOW_VERSIONS = {"CPI": ["5.0.0", "+", "~"], "ER": ["4.0.1", "+", "~"]}


def _auth_headers() -> dict[str, str]:
    # Optional IMF subscription key (env var or Streamlit secret). Not needed normally.
    key = os.environ.get("IMF_SDMX_SUBSCRIPTION_KEY")
    if not key:
        try:
            key = st.secrets.get("IMF_SDMX_SUBSCRIPTION_KEY")
        except Exception:  # noqa: BLE001
            key = None
    return {"Ocp-Apim-Subscription-Key": key} if key else {}


def _candidate_urls(flow: str, key: str) -> list[str]:
    urls = []
    for ver in FLOW_VERSIONS[flow]:
        urls.append(f"{BASE_URL}/data/IMF.STA,{flow},{ver}/{key}")
    for ver in FLOW_VERSIONS[flow][1:2]:
        urls.append(f"{BASE_URL}/data/dataflow/IMF.STA/{flow}/{ver}/{key}")
    return urls


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _fetch_cached(flow: str, key: str) -> pd.Series:
    # Fetch one IMF series. Empty Series = no data; raises if every request failed
    # with something other than 404 (exceptions are not cached by Streamlit).
    last_err: Exception | None = None
    extra = _auth_headers()
    for url in _candidate_urls(flow, key):
        for headers, kind in ((HEADERS_CSV, "csv"), (HEADERS_JSON, "json")):
            try:
                r = requests.get(url, headers={**headers, **extra}, timeout=60)
                if r.status_code == 404:  # wrong path/version or series missing
                    break
                r.raise_for_status()
                s = _parse_csv(r.text) if kind == "csv" else _parse_json(r.json())
                if not s.empty:
                    return s
            except Exception as exc:  # noqa: BLE001
                last_err = exc
    if last_err is not None:
        raise RuntimeError(str(last_err))
    return pd.Series(dtype=float)


def fetch(flow: str, key: str) -> tuple[pd.Series, str | None]:
    try:
        return _fetch_cached(flow, key), None
    except Exception as exc:  # noqa: BLE001
        return pd.Series(dtype=float), f"{key}: {exc}"


def load_country(code: str) -> tuple[pd.Series, pd.Series, list[str]]:
    # Return (CPI index, local currency units per USD, warnings).
    notes: list[str] = []
    cpi, err1 = fetch("CPI", f"{code}.CPI._T.IX.M")
    xdc, err2 = fetch("ER", f"{code}.XDC_USD.PA_RT.M")
    if xdc.empty and not err2:
        # Some IMF releases publish the inverse indicator (USD per local unit).
        inv, err3 = fetch("ER", f"{code}.USD_XDC.PA_RT.M")
        if not inv.empty:
            xdc = 1.0 / inv
        err2 = err2 or err3
    for e in (err1, err2):
        if e:
            notes.append(f"Request failed for {e}")
    if cpi.empty and not err1:
        notes.append(f"No monthly CPI found for {code}.")
    if xdc.empty and not err2:
        if code == "USA":
            xdc = pd.Series(1.0, index=cpi.index)  # USD per USD
        else:
            notes.append(f"No monthly exchange rate (XDC per USD) found for {code}.")
    return cpi, xdc, notes


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def build_panel(c1: str, c2: str) -> tuple[pd.DataFrame, list[str]]:
    cpi1, xdc1, n1 = load_country(c1)
    cpi2, xdc2, n2 = load_country(c2)
    df = pd.concat(
        {"P1": cpi1, "P2": cpi2, "XDC1_per_USD": xdc1, "XDC2_per_USD": xdc2}, axis=1
    ).sort_index()
    df = df[df.index >= FIRST_MONTH]
    if df.empty:
        return df, n1 + n2
    # drop non-positive values that would break ratios
    for col in df.columns:
        df.loc[df[col] <= 0, col] = float("nan")
    # Rates are local currency per USD, so S = (C2 per USD) / (C1 per USD).
    df["S"] = df["XDC2_per_USD"] / df["XDC1_per_USD"]
    df["RER"] = df["S"] * df["P1"] / df["P2"]
    df = df.replace([float("inf"), float("-inf")], float("nan"))
    df.index.name = "date"
    return df, n1 + n2


# --------------------------------------------------------------------------
# UI
# --------------------------------------------------------------------------
st.set_page_config(page_title="Real Exchange Rate Explorer", layout="wide")
st.title("Real Exchange Rate Explorer (IMF data)")
st.caption(
    "S(t) = units of Country 2 currency per 1 unit of Country 1 currency · "
    "RER(t) = S(t) × P1(t) / P2(t) · Real Index = 100 × RER / RER*"
)

options = country_options()
labels = list(options)
default1 = labels.index("United States (USA)")
default2 = labels.index("Japan (JPN)")

col_a, col_b = st.columns(2)
label1 = col_a.selectbox("Country 1 (base currency)", labels, index=default1)
label2 = col_b.selectbox("Country 2 (quote currency)", labels, index=default2)
code1, code2 = options[label1], options[label2]
name1, name2 = short_name(label1), short_name(label2)

if code1 == code2:
    st.warning("Please choose two different countries.")
    st.stop()

with st.spinner("Fetching IMF data…"):
    panel, notes = build_panel(code1, code2)

for n in notes:
    st.warning(n)

valid_all = panel[panel["RER"].notna()] if not panel.empty else panel
if valid_all.empty:
    st.error(
        "No overlapping valid months for this pair (CPI and exchange rate are both "
        "needed for each country). Try a different pair."
    )
    st.stop()

months = list(valid_all.index)
start, end = st.select_slider(
    "Sample period (start and end month)",
    options=months,
    value=(months[0], months[-1]),
    format_func=lambda t: t.strftime("%Y-%m"),
)

sample = panel.loc[start:end].copy()
valid = sample["RER"].notna()
n_valid = int(valid.sum())
rer_star = sample.loc[valid, "RER"].mean()
sample["RER_star"] = rer_star
sample["Real_Index"] = 100 * sample["RER"] / rer_star

m1, m2, m3 = st.columns(3)
m1.metric("RER* (sample mean)", f"{rer_star:,.4f}")
m2.metric("Valid months", f"{n_valid:,}")
m3.metric("Sample", f"{start:%Y-%m} → {end:%Y-%m}")

view = st.radio("Series to plot", ["Real", "Nominal", "Both"], horizontal=True)

nominal_title = f"Nominal S: {name2} currency per 1 {name1} currency"
real_title = f"Real Index (100 = sample mean RER*)"

fig = make_subplots(specs=[[{"secondary_y": True}]])
if view in ("Real", "Both"):
    fig.add_trace(
        go.Scatter(x=sample.index, y=sample["Real_Index"], name="Real Index",
                   mode="lines", line=dict(color="#1f77b4")),
        secondary_y=False,
    )
    fig.add_hline(y=100, line_dash="dot", line_color="gray")
if view in ("Nominal", "Both"):
    fig.add_trace(
        go.Scatter(x=sample.index, y=sample["S"], name="Nominal S",
                   mode="lines", line=dict(color="#d62728")),
        secondary_y=(view == "Both"),
    )

if view == "Real":
    fig.update_yaxes(title_text=real_title, secondary_y=False)
elif view == "Nominal":
    fig.update_yaxes(title_text=nominal_title, secondary_y=False)
else:
    fig.update_yaxes(title_text=real_title, secondary_y=False)
    fig.update_yaxes(title_text=nominal_title, secondary_y=True, showgrid=False)
fig.update_yaxes(visible=(view == "Both"), secondary_y=True)

fig.update_layout(
    height=520, hovermode="x unified", margin=dict(l=10, r=10, t=30, b=10),
    legend=dict(orientation="h", y=1.08),
)
fig.update_xaxes(title_text="Month", rangeslider=dict(visible=True))
st.plotly_chart(fig, use_container_width=True)

# --------------------------------------------------------------------------
# Data and download
# --------------------------------------------------------------------------
out = sample.reset_index().rename(
    columns={
        "P1": f"CPI_{code1}",
        "P2": f"CPI_{code2}",
        "XDC1_per_USD": f"{code1}_currency_per_USD",
        "XDC2_per_USD": f"{code2}_currency_per_USD",
        "S": f"S_{code2}_per_{code1}",
    }
)
out["date"] = out["date"].dt.strftime("%Y-%m")
out.insert(1, "country_1", code1)
out.insert(2, "country_2", code2)

with st.expander("Show data"):
    st.dataframe(out, use_container_width=True, hide_index=True)

st.download_button(
    "Download CSV (selected sample)",
    data=out.to_csv(index=False).encode("utf-8"),
    file_name=f"RER_{code1}_{code2}_{start:%Y%m}_{end:%Y%m}.csv",
    mime="text/csv",
)

st.caption(
    "Months with a missing CPI or exchange rate are left blank and excluded from RER*. "
    "Source: IMF (api.imf.org)."
)
