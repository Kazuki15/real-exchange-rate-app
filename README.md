# Real Exchange Rate (RER) Explorer

A Streamlit app that computes and plots the bilateral real exchange rate for any two IMF countries (or the Euro area) from monthly IMF CPI and exchange-rate data.

## Setup (Python 3.12)

```bash
python3.12 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

## Method

- **S(t)**: units of Country 2 currency per 1 unit of Country 1 currency.
  The IMF reports local currency units per USD, so `S = (C2 per USD) / (C1 per USD)`.
- **RER(t) = S(t) × P1(t) / P2(t)**, where P1 and P2 are monthly CPI indices.
- **RER\***: average of RER(t) over valid months in the selected sample. It is recomputed whenever the start or end month changes.
- **Real Index(t) = 100 × RER(t) / RER\***. A value above 100 means Country 1's currency is stronger in real terms than its sample average.
- The nominal S(t) is plotted in actual currency units, not normalized.
- "Both" shows the Real Index on the left axis and nominal S on the right axis.

## Data

| Item | IMF dataflow | Key |
|---|---|---|
| CPI (index, all items) | `IMF.STA/CPI` | `{CODE}.CPI._T.IX.M` |
| Exchange rate (period average, local currency per USD) | `IMF.STA/ER` | `{CODE}.XDC_USD.PA_RT.M` |

- Endpoint: `https://api.imf.org/external/sdmx/3.0`.
- `CODE` is an ISO3 country code, or `G163` for the Euro area.
- Data are requested from January 1976 where available.
- Responses are cached for 6 hours.

## Notes and limitations

- Coverage varies by country. Months missing a CPI or exchange rate for either country are left blank and excluded from RER\*.
- The sample-period slider only offers months where RER can be computed.
- For the USA, a missing USD/USD rate is set to 1.
- The Euro area series (G163) may not cover all periods. Pairs involving it can have a shorter sample.
- Different countries use different CPI base years. This does not matter for the Real Index, because the base-year scaling cancels in the 100 × RER / RER\* normalization.
- The CSV download contains every month in the selected sample, with inputs, S, RER, RER\*, and the Real Index.

## CSV columns

`date, country_1, country_2, CPI_<C1>, CPI_<C2>, <C1>_currency_per_USD, <C2>_currency_per_USD, S_<C2>_per_<C1>, RER, RER_star, Real_Index`
