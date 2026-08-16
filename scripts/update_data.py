#!/usr/bin/env python3
"""Build the dashboard dataset from Iranian market sources.

Primary methodology
-------------------
Equity: TEDPIX/TEPIX (Tehran Stock Exchange all-share price index).
FX:    free-market USD/IRR close (IRR per USD).

The script prefers official TSETMC index history when it is reachable and
passes a cross-check against TGJU. TSETMC is known to geo-block some foreign
IPs, so TGJU's Iranian-hosted historical index series is the fallback.

No interpolation is performed. FX is carried forward only to a TSE trading
session when the most recent FX observation is from the same day or an earlier
day; the number of stale calendar days is recorded for transparency.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import math
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "market.json"
START_DATE = dt.date(2012, 10, 9)  # Bonbast archive begins here; ample TGJU coverage too.
TEDPIX_CODES = ("32097828820363860", "32097828799138957")
UA = "Mozilla/5.0 (compatible; IranMarketDashboard/1.0; +https://github.com/TheCroqueMonsieur/iran-market-dashboard)"


def request_text(url: str, *, timeout: int = 30, tries: int = 3) -> str:
    last = None
    for n in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as exc:  # network sources are intentionally redundant
            last = exc
            if n + 1 < tries:
                time.sleep(1.5 * (n + 1))
    raise RuntimeError(f"GET failed: {url}: {last}")


def request_json(url: str, *, timeout: int = 30, tries: int = 3):
    return json.loads(request_text(url, timeout=timeout, tries=tries))


def num(value):
    if value is None:
        return None
    s = html.unescape(str(value))
    s = re.sub(r"<[^>]+>", "", s).strip().replace(",", "")
    s = s.replace("−", "-").replace("–", "-")
    if s in {"", "-", "--", "null", "None"}:
        return None
    try:
        x = float(s)
        return x if math.isfinite(x) else None
    except ValueError:
        return None


def date_iso(value: str) -> str | None:
    s = str(value).strip().replace("-", "/")
    try:
        d = dt.datetime.strptime(s[:10], "%Y/%m/%d").date()
        return d.isoformat()
    except ValueError:
        return None


def tgju_history(slug: str) -> dict[str, float]:
    """Fetch all available TGJU daily closes via its JSON history endpoint."""
    urls = [
        f"https://api.tgju.org/v1/market/indicator/summary-table-data/{slug}",
        f"https://api.accessban.com/v1/market/indicator/summary-table-data/{slug}",
    ]
    errors = []
    for base in urls:
        try:
            # Current TGJU endpoint usually returns full history without pagination.
            payload = request_json(base + "?lang=fa&convert_to_ad=1&length=10000")
            rows = payload.get("data", []) if isinstance(payload, dict) else []
            if not rows:
                raise RuntimeError("empty data array")
            out = {}
            for row in rows:
                if not isinstance(row, list) or len(row) < 7:
                    continue
                d = date_iso(row[6])
                close = num(row[3])
                if d and close and close > 0:
                    out[d] = close
            if len(out) >= 100:
                return out
            raise RuntimeError(f"only {len(out)} parsed rows")
        except Exception as exc:
            errors.append(f"{base}: {exc}")

    # Older Accessban endpoint may paginate in 30-row chunks.
    base = urls[1]
    try:
        out = {}
        for start in range(0, 10000, 250):
            q = urllib.parse.urlencode({
                "lang": "fa", "convert_to_ad": "1", "start": start, "length": 250
            })
            payload = request_json(base + "?" + q)
            rows = payload.get("data", []) if isinstance(payload, dict) else []
            if not rows:
                break
            before = len(out)
            for row in rows:
                if isinstance(row, list) and len(row) >= 7:
                    d = date_iso(row[6])
                    close = num(row[3])
                    if d and close and close > 0:
                        out[d] = close
            if len(rows) < 250 or len(out) == before:
                break
        if len(out) >= 100:
            return out
        raise RuntimeError(f"only {len(out)} paginated rows")
    except Exception as exc:
        errors.append(f"paginated {base}: {exc}")
    raise RuntimeError("TGJU history failed: " + " | ".join(errors))


def tsetmc_index_history() -> tuple[dict[str, float], str]:
    """Fetch official TSETMC TEDPIX history.

    Members chart history is preferred because it remains more reliable than
    several newer CDN endpoints. Its 7-column index rows have Gregorian date
    first and the index close as the final field.
    """
    errors = []
    for code in TEDPIX_CODES:
        # Members chart endpoint: old, simple, and generally the most complete.
        for host in ("https://members.tsetmc.com", "http://www.tsetmc.com"):
            url = f"{host}/tsev2/chart/data/IndexFinancial.aspx?i={code}&t=ph"
            try:
                text = request_text(url, timeout=25, tries=2)
                out = {}
                for raw in text.strip().split(";"):
                    cols = [c.strip() for c in raw.split(",")]
                    if len(cols) < 7:
                        continue
                    d = date_iso(cols[0])
                    close = num(cols[-1])
                    if d and close and close > 100:
                        out[d] = close
                if len(out) >= 500:
                    return out, f"TSETMC IndexFinancial ({code})"
                raise RuntimeError(f"only {len(out)} rows")
            except Exception as exc:
                errors.append(f"{url}: {exc}")

        # Modern CDN fallback. Field names have changed over time, so detect
        # common date/value keys rather than hard-code a single schema.
        url = f"https://cdn.tsetmc.com/api/Index/GetIndexB2History/{code}"
        try:
            payload = request_json(url, timeout=25, tries=2)
            rows = payload.get("indexB2", []) if isinstance(payload, dict) else []
            out = {}
            for row in rows:
                if not isinstance(row, dict):
                    continue
                dv = next((row.get(k) for k in ("dEven", "deven", "date", "dEvenPersian") if row.get(k) is not None), None)
                if isinstance(dv, (int, float)) or (isinstance(dv, str) and dv.isdigit() and len(dv) == 8):
                    s = str(int(dv)) if not isinstance(dv, str) else dv
                    d = f"{s[:4]}-{s[4:6]}-{s[6:8]}"
                else:
                    d = date_iso(str(dv)) if dv else None
                candidates = (
                    "xNivInuClMresIbs", "xNivInuClMresIbsValue", "indexValue",
                    "value", "close", "xNivInuClMresIbsClose"
                )
                close = next((num(row.get(k)) for k in candidates if num(row.get(k)) is not None), None)
                if d and close and close > 100:
                    out[d] = close
            if len(out) >= 500:
                return out, f"TSETMC CDN GetIndexB2History ({code})"
            raise RuntimeError(f"only {len(out)} rows")
        except Exception as exc:
            errors.append(f"{url}: {exc}")
    raise RuntimeError("Official TSETMC history unavailable: " + " | ".join(errors[-4:]))


def bonbast_archive() -> dict[str, float]:
    """Bonbast open-market USD midpoint, converted from toman to IRR."""
    url = "https://raw.githubusercontent.com/SamadiPour/rial-exchange-rates-archive/data/gregorian_imp.min.json"
    payload = request_json(url, timeout=45, tries=3)
    out = {}
    if not isinstance(payload, dict):
        return out
    for k, v in payload.items():
        d = date_iso(k)
        if not d or not isinstance(v, dict):
            continue
        usd = v.get("usd") or v.get("USD")
        if not isinstance(usd, dict):
            continue
        buy, sell = num(usd.get("buy")), num(usd.get("sell"))
        vals = [x for x in (buy, sell) if x and x > 0]
        if vals:
            out[d] = statistics.mean(vals) * 10.0  # archive is in toman
    return out


def crosscheck_index(official: dict[str, float], tgju: dict[str, float]) -> tuple[bool, float | None, int]:
    common = sorted(set(official).intersection(tgju))[-60:]
    diffs = []
    for d in common:
        a, b = official[d], tgju[d]
        if a > 0 and b > 0:
            diffs.append(abs(a / b - 1.0))
    if len(diffs) < 10:
        return False, None, len(diffs)
    med = statistics.median(diffs)
    return med < 0.01, med, len(diffs)


def prior_value(series: dict[str, float], d: str):
    """Latest value on or before d. Series keys must be ISO dates."""
    # Dataset sizes are a few thousand observations, so this simple indexed
    # lookup is clearer and plenty fast after pre-sorting in build().
    raise NotImplementedError


def build():
    notes = []

    print("Fetching TGJU TEDPIX validation/fallback series...", file=sys.stderr)
    tgju_index = tgju_history("gc30")

    official = {}
    official_name = None
    try:
        print("Fetching official TSETMC TEDPIX...", file=sys.stderr)
        official, official_name = tsetmc_index_history()
    except Exception as exc:
        notes.append(str(exc))

    if official:
        ok, med, n = crosscheck_index(official, tgju_index)
        if ok:
            index = official
            index_source = official_name
            notes.append(f"Official TSETMC passed TGJU cross-check: median absolute relative difference {med:.4%} across {n} recent overlaps.")
        else:
            index = tgju_index
            index_source = "TGJU gc30 (Iranian TEDPIX historical series; official TSETMC failed validation)"
            notes.append(f"Official TSETMC was reachable but failed the cross-check (median difference {med}; overlaps {n}); TGJU used instead.")
    else:
        index = tgju_index
        index_source = "TGJU gc30 (Iranian TEDPIX historical series; TSETMC fallback)"

    print("Fetching TGJU free-market USD/IRR...", file=sys.stderr)
    fx_free = tgju_history("price_dollar_rl")

    fx_bonbast = {}
    try:
        print("Fetching Bonbast archive mirror...", file=sys.stderr)
        fx_bonbast = bonbast_archive()
    except Exception as exc:
        notes.append(f"Bonbast comparison unavailable: {exc}")

    fx_nima = {}
    for slug in ("nima_sell_usd", "sana_sell_usd"):
        try:
            print(f"Fetching regulated comparison series {slug}...", file=sys.stderr)
            fx_nima = tgju_history(slug)
            if fx_nima:
                break
        except Exception as exc:
            notes.append(f"{slug} comparison unavailable: {exc}")

    # Sorted lookup lists for carry-forward alignment.
    fx_series = {
        "tgju_free": fx_free,
        "bonbast_mid": fx_bonbast,
        "regulated": fx_nima,
    }
    fx_sorted = {name: sorted(s.items()) for name, s in fx_series.items() if s}
    ptr = {name: 0 for name in fx_sorted}
    last = {name: None for name in fx_sorted}

    records = []
    for d, tepix in sorted(index.items()):
        try:
            dd = dt.date.fromisoformat(d)
        except ValueError:
            continue
        if dd < START_DATE:
            continue
        rec = {"date": d, "tepix": round(float(tepix), 4), "fx": {}, "fx_age_days": {}}
        for name, items in fx_sorted.items():
            p = ptr[name]
            while p < len(items) and items[p][0] <= d:
                last[name] = items[p]
                p += 1
            ptr[name] = p
            if last[name] is not None:
                fd, value = last[name]
                age = (dd - dt.date.fromisoformat(fd)).days
                # Refuse very stale FX. A 4-day window tolerates weekends/holidays.
                if 0 <= age <= 4:
                    rec["fx"][name] = round(float(value), 4)
                    rec["fx_age_days"][name] = age
        if rec["fx"].get("tgju_free"):
            records.append(rec)

    if len(records) < 500:
        raise RuntimeError(f"Only {len(records)} aligned observations; refusing to publish")

    # Data-quality diagnostics.
    latest = records[-1]
    if latest["tepix"] <= 0 or latest["fx"]["tgju_free"] <= 0:
        raise RuntimeError("Latest values are non-positive")

    payload = {
        "schema_version": 1,
        "updated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "methodology": {
            "index": "TEDPIX/TEPIX close (Tehran Stock Exchange all-share price index)",
            "default_fx": "TGJU free-market USD/IRR daily close, IRR per USD",
            "usd_return_formula": "(TEPIX_t / TEPIX_t-1) * (USDIRR_t-1 / USDIRR_t) - 1",
            "usd_index_formula": "100 * (TEPIX_t / USDIRR_t) / (TEPIX_base / USDIRR_base)",
            "fx_alignment": "Most recent FX observation on or before each TSE session; max 4 calendar days; age disclosed",
        },
        "sources": {
            "index": index_source,
            "index_validation": "TGJU gc30",
            "tgju_free": "TGJU price_dollar_rl — free-market USD banknotes, rial per USD",
            "bonbast_mid": "Bonbast archive mirror — midpoint of buy/sell, toman converted x10 to rial" if fx_bonbast else None,
            "regulated": "TGJU NIMA/SANA regulated sell-rate comparison" if fx_nima else None,
        },
        "notes": notes,
        "records": records,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {len(records):,} observations through {records[-1]['date']} to {OUT}")


if __name__ == "__main__":
    build()
