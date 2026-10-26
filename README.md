# Iran Equity Monitor

A daily dashboard for Tehran Stock Exchange performance in USD terms.

## Methodology

- **Equity index:** TEDPIX / TEPIX (Tehran Stock Exchange all-share price index).
- **Default FX:** free-market USD/IRR from TGJU (`price_dollar_rl`).
- **Cross-check / comparison FX:** Bonbast open-market midpoint when available; regulated NIMA/SANA series is attempted for comparison only.
- **USD daily return:** `(TEPIX_t / TEPIX_t-1) × (USDIRR_t-1 / USDIRR_t) - 1`.
- **USD index:** TEPIX divided by USD/IRR and rebased to 100 at the start of the overlapping dataset.
- **No interpolation:** the latest FX observation on or before a TSE trading session may be carried forward for up to four calendar days; its age is shown in the dashboard.

## Data sources and quality controls

The updater prefers official TSETMC index history when it is reachable and validates recent overlapping observations against TGJU. Because TSETMC endpoints can be inaccessible from foreign IP ranges, the workflow falls back to TGJU's Iranian TEDPIX history rather than publishing missing or fabricated values.

Free-market FX is the default because the purpose of the dashboard is to estimate the dollar value of Iranian equity wealth using a rate closer to convertibility than Iran's administrative exchange rates. The FX convention can be changed in the interface when comparison series are available.

The updater refuses to publish if it cannot assemble at least 500 valid overlapping observations.

## Automation

GitHub Actions refreshes the data after the Tehran trading day Sunday through Thursday, commits `data/market.json`, and deploys the site through GitHub Pages.

## Disclaimer

Research and monitoring tool only. Iranian market data can be revised, interrupted, or subject to access restrictions. Source and FX-age diagnostics are retained explicitly.
## Coverage and return diagnostics

The dashboard distinguishes the selected FX series endpoint from the dataset
endpoint and shows the selected endpoint's underlying FX quote date. Missing or
invalid FX ages are not treated as same-day quotes. Daily FX/USD returns require
both adjacent dataset rows; missing observations remain gaps. Longer returns
use fixed dataset-row offsets and require valid FX at both endpoints. YTD uses
the previous year's final stored row; unavailable baselines produce no return.
The 21/252-observation periods are approximations, not verified exchange-session
counts: unusual Thursday/Friday source dates are flagged for investigation,
not silently deleted or reassigned.

`scripts/build_market_data.py` is the production builder; `scripts/update_data.py`
is a compatibility entry point to the same implementation. Builds reject invalid
FX ages/values, future or duplicate dates, and loss of previously published index
dates. Source coverage and index freshness warnings are published in metadata.
Warnings do not certify source accuracy or independently verify the calendar.

Run the offline regression checks with:

```sh
python -m unittest discover -s tests
node tests/returns.cjs
```
