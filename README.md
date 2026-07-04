# pricing-cds

Tools for **CDS-related curve construction**, built around a small, well-tested
Python library and an interactive web app.

- **`cdslib`** — a minimal library for term-structure tooling, generic OIS
  instruments, smoothing OIS curve bootstrapping, and reduced-form bond pricing /
  issuer credit-curve (hazard) bootstrapping.
- **`app/`** — a FastAPI + Plotly web app with two tools: bootstrap a zero-coupon
  yield curve from OIS quotes, and bootstrap an issuer **credit (hazard) curve**
  from its bond prices (fetched from MOEX ISS). State persists in SQLite. See the
  **[app guide](app/README.md)** and **[deployment guide](DEPLOY.md)**.

The initial focus is the **RUONIA / RUB** market, but rate indices, conventions,
and holidays are data-driven and easy to extend.

---

## Repository layout

```
src/cdslib/          # the library
  tenor.py           # tenor codes (1w, 3m, 5y ...)
  daycount.py        # ACT/360, ACT/365F
  calendar.py        # holiday calendars, business-day rolling
  schedule.py        # accrual schedule generation
  conventions.py     # rate-index -> currency + OIS conventions
  ois.py             # OISGenerator.generate(rate, tenor, trade_date)
  curve.py           # ZeroCurve (log-linear discounting)
  bootstrap.py       # smoothing OIS curve bootstrap
  survival.py        # SurvivalCurve (piecewise-constant hazard)
  bond.py            # bond cashflows + reduced-form dirty price
  credit_bootstrap.py # issuer credit-curve bootstrap from bond prices
  data/              # rate_indices.json, holidays.json
app/                 # web app (FastAPI backend + Plotly frontend, SQLite)
notebooks/           # demo notebooks (generators, bootstrap)
tests/               # pytest suite
```

---

## Quickstart

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync                 # install the library + dev tools
uv run pytest           # run the test suite
```

### Use the library

```python
from datetime import date
from cdslib import OISQuote, bootstrap, generate_ois

# Generate a dated OIS from a generic (rate, tenor) spec
swap = generate_ois("RUONIA", "5y", date(2024, 6, 3))

# Bootstrap a monthly zero curve from bid/ask quotes (bid/ask in % points)
quotes = [
    OISQuote("RUONIA", "1y", 14.48, 14.68),
    OISQuote("RUONIA", "3y", 13.29, 14.49),
    OISQuote("RUONIA", "5y", 14.40, 14.59),
]
result = bootstrap(quotes, date(2024, 6, 3), max_avg_adjustment_bps=15.0)
print(result.as_tuples())            # [(tenor_days, zero_rate), ...]
print(result.average_adjustment_bps) # fit diagnostic
```

### Run the web app

```bash
docker compose up --build            # then open http://localhost:8127
# or, for local dev:
uv run --group app uvicorn app.backend.main:app --reload --port 8127
```

Full instructions, UI walkthrough, input/output formats, and the HTTP API are in
the **[app guide](app/README.md)**.

### Explore the notebooks

```bash
uv run --group notebook jupyter lab notebooks/
```

- `ois_demo.ipynb` — the generic OIS generators.
- `bootstrap_demo.ipynb` — the bootstrap, curve/forward plots, adjustments.

---

## How the bootstrap works

Mids in illiquid markets are unreliable, so the bootstrap fits a monthly
continuously-compounded zero curve that:

- matches model OIS par rates to quote **mids**, each **weighted by reliability**
  (inverse bid/ask spread), so wide-spread quotes absorb most of the adjustment;
- penalizes the **forward-rate slope** to keep the forward curve smooth and
  hump-free;
- picks the smoothing level at the **L-curve knee** (least adjustment that removes
  genuine kinks), never exceeding a configurable **average adjustment cap**
  (default 15 bps).

Details: [`app/README.md` § Methodology](app/README.md#6-methodology).

---

## Development

```bash
uv run ruff check .        # lint
uv run mypy src app        # type-check (strict)
uv run pytest              # tests
```

Conventions and holidays are edited in `src/cdslib/data/*.json`. Holidays are
currently seeded for 2024–2026.
