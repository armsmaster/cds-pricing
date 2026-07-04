# OIS & Credit Curves — App Guide

An interactive web app with two tools, switched by the top-nav tabs:

- **Rate curve** — build a **zero-coupon (ZC) yield curve** from overnight-index-swap
  (OIS) bid/ask quotes; preview par rates live, bootstrap a smooth curve, export JSON.
- **Credit curves** — build an issuer **hazard-rate / survival curve** from its bond
  prices (fetched from MOEX ISS), discounting off a saved rate curve.

It is a thin **FastAPI** backend over the [`cdslib`](../README.md) library plus a
single-page **Plotly** frontend (no build step). State (issuers, bonds, saved rate
curves, cached prices) is persisted in SQLite.

---

## 1. Running the app

### With Docker (recommended)

From the repository root:

```bash
docker compose up --build
```

Then open **http://localhost:8127**. Stop with `docker compose down`.

### With uv (local dev)

```bash
uv run --group app uvicorn app.backend.main:app --reload --port 8127
```

Then open **http://localhost:8127**. `--reload` restarts on backend changes;
frontend files are served from disk with `no-cache`, so a browser refresh is
enough to pick up edits.

---

## 2. Concepts in 30 seconds

- An **OIS quote** is a par swap rate for a tenor, given as a **bid** and **ask**
  in **percentage points** (`14.35` means 14.35%). The **mid** is `(bid+ask)/2`.
- **Bootstrapping** finds the discount/zero curve whose model par rates match the
  quotes. Because bid/ask spreads in illiquid markets are wide, the app treats
  mids as *approximate*: it may **adjust** them slightly to produce a smooth,
  realistic **forward** curve.
- Two guardrails control the adjustment:
  - **Reliability weighting** — a quote with a wide spread is trusted less, so it
    absorbs most of the adjustment while tight quotes stay pinned.
  - **Max adjustment (bps)** — a hard cap on the *average* absolute adjustment
    (default **15 bps**).

See [Methodology](#6-methodology) for details.

---

## 3. User guide (UI walkthrough)

The left panel holds the inputs; the right side shows results.

### Inputs (left panel)

| Control | Purpose |
|---|---|
| **Trade date** | Valuation date; defaults to today. Spot/maturity dates are derived from it using the rate's conventions and holiday calendar. |
| **Max adjustment (bps)** | Upper bound on the average absolute mid adjustment. Default 15. Lower it to track mids more closely; raise it to allow more smoothing. |
| **OIS quotes (JSON)** | The quote list (see [format](#4-input-format)). Edited live. |
| **Calculate** | Runs the bootstrap. Shortcut: **Ctrl/Cmd + Enter**. |

The quotes box border turns **green** when the JSON is valid and **red** when it
isn't. The status line under the button reports load/calculation state or errors.

### Live par-rate preview

As soon as the quotes JSON is valid, the **Par rates** chart renders:

- **triangles** — bid (down) and ask (up)
- shaded **bid–ask band** between them
- dotted **mid** line
- after calculation, a solid **adjusted mid** line is added — you can see at a
  glance whether each adjusted mid stays inside the bid–ask band.

### After **Calculate**

The summary chips (left) show the rate index, spot date, **average adjustment**
(color-coded vs the cap), the smoothing weight λ, and the node count. The results
area fills in:

1. **Zero-coupon yield curve** — the fitted ZC curve on a monthly grid. Toggle
   **Annual / Continuous / Both** compounding for the *chart* (independent of the
   export setting below).
2. **Forward rate curve** — the instantaneous forward implied by the curve; this
   is the quantity the smoother keeps flat/realistic.
3. **Par rate adjustments** table — per quote: bid, ask, raw mid, adjusted mid,
   adjustment in bps, and whether the adjusted mid is still **in spread**
   (out-of-spread rows are highlighted).
4. **Zero-coupon yield curve (JSON)** — the exportable result (see
   [output](#5-output-format)).

### Exporting the curve

In the JSON card:

- **Compounding** (segmented) — choose **Annual** or **Continuous** for the
  exported yields. This is independent from the chart's compounding toggle.
- **Copy** — copies the JSON to the clipboard.
- **Download .json** — saves `zcyc_<RATE>_<trade_date>.json`.

---

## 4. Input format

The quotes field is a **JSON array** of objects:

```json
[
  { "rate": "RUONIA", "tenor": "3m",  "bid": 14.13, "ask": 14.38 },
  { "rate": "RUONIA", "tenor": "1y",  "bid": 14.48, "ask": 14.68 },
  { "rate": "RUONIA", "tenor": "3y",  "bid": 13.29, "ask": 14.49 }
]
```

| Field | Meaning | Notes |
|---|---|---|
| `rate` | Overnight rate index | Aliases accepted: `rate_name`, `rate_index`, `index`. Must be known (see [conventions](#7-conventions--data)). |
| `tenor` | Tenor code | Aliases: `tenor_code`, `code`. Examples: `1w`, `1m`, `3m`, `6m`, `9m`, `1y`, `2y`, `5y`, `10y`. |
| `bid` | Bid par rate | Percentage points (`14.35` = 14.35%). |
| `ask` | Ask par rate | Percentage points. |

All quotes in one request must share the same `rate` (one curve per index).

---

## 5. Output format

The exported **ZCYC JSON** maps each monthly node's **future date** to its yield
(as a decimal fraction) at the selected compounding:

```json
{
  "2024-07-04": 0.15190605,
  "2024-08-05": 0.15021340,
  "2029-06-04": 0.14499385
}
```

- **Annual** compounding: `y` such that `DF = (1 + y)^(-T)`.
- **Continuous** compounding: `z` such that `DF = exp(-z · T)`.
- Time `T` uses ACT/365. Dates are the monthly grid measured from the spot date.

---

## 6. Methodology

The bootstrap (in `cdslib.bootstrap`) fits a monthly continuously-compounded zero
curve by least squares:

- **Fit term** — model OIS par rates vs quote mids, each weighted by
  `1 / bid-ask spread` (reliability weighting).
- **Smoothness term** — penalizes the **slope** of the forward curve (first
  difference), which drives it toward a flat, hump-free shape.
- **Smoothing level** — chosen at the **L-curve knee** (maximum-curvature /
  Menger point): the least adjustment that removes genuine kinks.
- **Cap** — if the knee would exceed `max_adjustment_bps` on average, smoothing is
  reduced until the average absolute adjustment is within the cap.

The curve nodes are monthly from the spot date out to the longest quoted maturity.
Discounting between nodes is log-linear.

---

## 7. Conventions & data

Rate indices and their OIS conventions live in
[`src/cdslib/data/rate_indices.json`](../src/cdslib/data/rate_indices.json);
per-currency holidays live in
[`src/cdslib/data/holidays.json`](../src/cdslib/data/holidays.json).

Shipped indices:

| Index | Currency | Day count | Spot lag | Fixed freq |
|---|---|---|---|---|
| `RUONIA` | RUB | ACT/365F | 1 | 1Y |
| `SOFR` | USD | ACT/360 | 2 | 1Y |

Holidays are seeded for **2024–2026**; dates outside that range fall back to a
weekend-only calendar. Add indices/holidays by editing those JSON files.

---

## 8. HTTP API

The frontend talks to two JSON endpoints. Both accept an optional
`trade_date` (`YYYY-MM-DD`, default today) and a `quotes` array.

### `POST /api/quotes` — par-rate preview

Request:

```json
{ "trade_date": "2024-06-03",
  "quotes": [ { "rate": "RUONIA", "tenor": "1y", "bid": 14.48, "ask": 14.68 } ] }
```

Response: `{ "trade_date", "quotes": [ { tenor, maturity_date, years, bid, ask, mid } ] }`
(rates in %, sorted by maturity).

### `POST /api/bootstrap` — build the curve

Request (adds `max_adjustment_bps`, default 15):

```json
{ "trade_date": "2024-06-03", "max_adjustment_bps": 15,
  "quotes": [ { "rate": "RUONIA", "tenor": "1y", "bid": 14.48, "ask": 14.68 } ] }
```

Response fields:

| Field | Description |
|---|---|
| `rate_index`, `spot_date` | Resolved index and spot date. |
| `average_adjustment_bps`, `smoothing_lambda` | Fit diagnostics. |
| `curve` | Monthly nodes: `{ date, years, zero_cont, zero_annual }` (rates in %). |
| `forwards` | `{ date, years, forward }` (forward rate in %). |
| `fits` | Per quote: `{ tenor, years, maturity_date, bid, ask, raw_mid, adj_mid, adj_bps, within_spread }`. |
| `zcyc` | `{ future_date: annual_yield_fraction }`. |

Errors return HTTP **400** with a `{ "detail": "..." }` message (e.g. unknown
rate, bad tenor, empty quotes, mixed rate indices).

Interactive OpenAPI docs are available at **/docs** while the server runs.

---

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| "Invalid JSON" / red quotes box | The textarea must be a JSON **array** of quote objects. |
| 400 "Unknown rate index" | Use a rate present in `rate_indices.json` (e.g. `RUONIA`). |
| 400 "All quotes must reference the same rate index" | Split multi-index quotes into separate curves. |
| Charts look narrow until you resize | Fixed in current build (cards are shown before Plotly renders). Hard-refresh if you edited old assets. |
| Port 8127 already in use | Another server/container owns it. Stop it, or change the port mapping in `docker-compose.yml`. |

---

## 10. Credit curves (issuer hazard curves)

The **Credit curves** tab implies an issuer's default-risk curve from the market
prices of its bonds, using a saved risk-free curve for discounting.

### Prerequisite: a saved rate curve

Credit curves discount off a **saved** OIS curve. On the **Rate curve** tab,
bootstrap a curve and press **Save curve for credit use**. It is stored per
`(rate index, trade date)` and appears in the Credit tab's *Risk-free curve*
dropdown.

### Workflow

1. **Create an issuer** (New issuer → name + recovery rate) and select it.
   - **Recovery rate** `R` is the assumed fraction of face recovered on default;
     loss-given-default is `LGD = 1 − R`. Edit it and press **Set**.
2. **Add bonds by ISIN** (e.g. `RU000A10B115`). Reference data and the coupon
   schedule are fetched from **MOEX ISS** and stored. Bonds are priced to their
   **offer/put date** (post-offer coupons are typically unknown).
3. **Review market prices** — the *Market prices* table shows each bond's fetched
   clean price. Edit a price to **override** it, or untick **Include** to drop a
   bond; edits are saved per trade date. *Refresh from MOEX* re-pulls prices while
   keeping your overrides.
4. **Compute** — pick a **trade date**, choose the saved **risk-free curve**, and
   press **Calculate credit curve**.

### Reading the results

- **Hazard rate curve** — the bootstrapped default intensity (hazard) and the
  implied **credit spread** (`hazard × LGD`) by date.
- **Survival probability** — probability the issuer has not defaulted by each date.
- **Bond repricing** — per bond: the market **clean** price used (% of nominal) and
  its source, the **model** clean price from the fitted curve, the residual as % of
  the market price, and the price **weight**. Bonds with no usable price on the date
  are listed under the table. **Download Excel** exports a macro-free `.xlsx` that
  reconstructs each bond's model price with live formulas (schedule × discount ×
  survival, with recovery), interpolating discount factors and survival from IR-curve
  and credit-curve sheets — so the whole calculation is auditable in Excel.
- **Hazard curve (JSON)** — export `future date → average hazard (fraction)` via
  Copy or Download.

### How prices are chosen

Bond markets here are illiquid, so for each bond on the trade date the app takes a
robust clean price from whatever is available — **bid/ask mid → last → WAP →
official close** — converts to a dirty price with the exchange's accrued interest,
and **weights** the bond by how trustworthy that price is (tight two-sided quotes
highest). Weights feed the fit so unreliable prices influence the curve less.

### Method

Reduced-form (intensity) model. For discount `P(t)`, survival `Q(t)=exp(−∫λ)`,
recovery `R`:

```
DirtyPrice = Σ cᵢ·P(tᵢ)·Q(tᵢ) + N·P(T)·Q(T) + R·N·Σ P(tᵢ)·(Q(tᵢ₋₁)−Q(tᵢ))
```

A hazard curve on a **monthly grid** is fitted by reliability-weighted least
squares so model dirty prices match the market, with a forward-hazard **slope**
penalty (strength chosen at the L-curve knee) that keeps the instantaneous hazard
**smooth and hump-free** — the same approach as the OIS forward curve.

### Data & persistence

- Reference/schedule from `iss.moex.com`, fetched on demand and cached; a past
  **trade date** uses EOD history, today uses the live snapshot.
- Issuers, bonds, cached prices and saved rate curves persist in SQLite
  (`data/app.db`; see `../DEPLOY.md`).

### Credit API

| Method & path | Purpose |
|---|---|
| `POST /api/rate-curves` (body = a bootstrap request) | Bootstrap **and save** a rate curve. |
| `GET /api/rate-curves`, `DELETE /api/rate-curves/{id}` | List / delete saved curves. |
| `POST/GET /api/issuers`, `PATCH/DELETE /api/issuers/{id}` | Manage issuers + recovery. |
| `POST/GET /api/issuers/{id}/bonds`, `DELETE /api/bonds/{isin}` | Add (fetch from MOEX) / list / remove bonds. |
| `GET /api/moex/search?q=` | MOEX security search. |
| `GET /api/bonds/{isin}/marketdata?date=` | Fetch + cache a bond's price. |
| `POST /api/credit-curve` `{ issuer_id, rate_curve_id, trade_date }` | Bootstrap the credit curve → hazard/survival grid, per-bond fits, skipped bonds, hazard export. |
