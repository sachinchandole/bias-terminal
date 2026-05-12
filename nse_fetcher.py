"""
NSE data fetcher.

Uses `nselib` to pull publicly available data from NSE India.
Provides simple cached accessors used by the Flask app.

The functions here are defensive:
- They wrap nselib calls in try/except (NSE periodically changes endpoints)
- They cache results in-memory with TTL
- They return dicts the front-end can render directly
"""

from __future__ import annotations
import logging
from datetime import datetime, timedelta
from threading import Lock
from typing import Any

import pandas as pd

try:
    from nselib import derivatives, capital_market
    NSE_AVAILABLE = True
except Exception as exc:  # pragma: no cover
    NSE_AVAILABLE = False
    _IMPORT_ERROR = str(exc)

log = logging.getLogger(__name__)

# ============================================================
# CACHE
# ============================================================
_CACHE: dict[str, tuple[datetime, Any]] = {}
_LOCK = Lock()


def _cached(key: str, ttl_sec: int, fetch_fn):
    """Return (data, was_cached). On fetch failure, fall back to stale cache."""
    now = datetime.utcnow()
    with _LOCK:
        entry = _CACHE.get(key)
        if entry and (now - entry[0]).total_seconds() < ttl_sec:
            return entry[1], True
    try:
        data = fetch_fn()
        with _LOCK:
            _CACHE[key] = (now, data)
        return data, False
    except Exception as exc:
        log.exception("Fetch failed for %s: %s", key, exc)
        if entry:
            log.warning("Returning stale cache for %s", key)
            return entry[1], True
        raise


# ============================================================
# OPTION CHAIN ANALYSIS
# ============================================================
def _find_col(df: pd.DataFrame, *needles: str):
    """Find a column in df whose uppercased name contains all needles."""
    for col in df.columns:
        up = str(col).upper()
        if all(n in up for n in needles):
            return col
    return None


def fetch_option_chain_summary(symbol: str) -> dict:
    """Return PCR, top OI strikes, max pain, IV from NSE option chain."""
    if not NSE_AVAILABLE:
        raise RuntimeError(f"nselib not available: {_IMPORT_ERROR}")

    sym = symbol.upper()
    if sym not in ("NIFTY", "BANKNIFTY"):
        raise ValueError("symbol must be NIFTY or BANKNIFTY")

    df = derivatives.nse_live_option_chain(symbol=sym, oi_mode="compact")

    if df is None or len(df) == 0:
        raise RuntimeError("empty option chain")

    # nselib returns columns like: Strike_Price, CALLS_OI, CALLS_Chng_in_OI, CALLS_Volume,
    # CALLS_IV, CALLS_LTP, PUTS_OI, ..., etc. Names vary slightly by version, so be flexible.
    strike_col = _find_col(df, "STRIKE")
    call_oi_col = _find_col(df, "CALLS", "OI") or _find_col(df, "CALL", "OI")
    put_oi_col = _find_col(df, "PUTS", "OI") or _find_col(df, "PUT", "OI")
    call_iv_col = _find_col(df, "CALLS", "IV") or _find_col(df, "CALL", "IV")
    put_iv_col = _find_col(df, "PUTS", "IV") or _find_col(df, "PUT", "IV")

    # ChngInOI columns help identify writers (positive = OI added that day)
    call_chg_col = _find_col(df, "CALLS", "CHNG") or _find_col(df, "CALLS", "CHANGE")
    put_chg_col = _find_col(df, "PUTS", "CHNG") or _find_col(df, "PUTS", "CHANGE")

    if not strike_col or not call_oi_col or not put_oi_col:
        return {
            "error": "expected columns not found",
            "available_columns": [str(c) for c in df.columns],
        }

    df = df.copy()
    # Filter out the change-in-OI rows that some nselib versions return.
    # Keep only rows with numeric strikes.
    df[strike_col] = pd.to_numeric(df[strike_col], errors="coerce")
    df = df.dropna(subset=[strike_col])
    df[call_oi_col] = pd.to_numeric(df[call_oi_col], errors="coerce").fillna(0)
    df[put_oi_col] = pd.to_numeric(df[put_oi_col], errors="coerce").fillna(0)

    total_ce = float(df[call_oi_col].sum())
    total_pe = float(df[put_oi_col].sum())
    pcr = round(total_pe / total_ce, 3) if total_ce else None

    top_calls = (
        df.nlargest(5, call_oi_col)[strike_col]
        .astype(float).round().astype(int).tolist()
    )
    top_puts = (
        df.nlargest(5, put_oi_col)[strike_col]
        .astype(float).round().astype(int).tolist()
    )

    # ATM IV (average of nearest-to-spot CE & PE IV) — needs spot. We approximate using
    # the max-OI strike as a proxy if spot unknown; caller passes spot when available.
    atm_iv = None
    if call_iv_col and put_iv_col:
        # take the strike with highest sum(call OI + put OI) as ATM proxy
        df["_total_oi"] = df[call_oi_col] + df[put_oi_col]
        atm_row = df.loc[df["_total_oi"].idxmax()]
        ce_iv = pd.to_numeric(atm_row[call_iv_col], errors="coerce")
        pe_iv = pd.to_numeric(atm_row[put_iv_col], errors="coerce")
        if pd.notna(ce_iv) and pd.notna(pe_iv) and ce_iv > 0 and pe_iv > 0:
            atm_iv = round(float((ce_iv + pe_iv) / 2), 2)

    # Max Pain calculation
    strikes = sorted(df[strike_col].unique().tolist())
    pain = []
    ce_oi_arr = df.set_index(strike_col)[call_oi_col].to_dict()
    pe_oi_arr = df.set_index(strike_col)[put_oi_col].to_dict()
    for s in strikes:
        total = 0.0
        for k in strikes:
            ce = ce_oi_arr.get(k, 0)
            pe = pe_oi_arr.get(k, 0)
            if s > k:  # calls with strike k expire ITM
                total += (s - k) * ce
            if s < k:  # puts with strike k expire ITM
                total += (k - s) * pe
        pain.append((s, total))
    max_pain_strike = int(min(pain, key=lambda x: x[1])[0]) if pain else None

    # OI buildup signal: net put writing vs call writing
    oi_behaviour = None
    if call_chg_col and put_chg_col:
        df[call_chg_col] = pd.to_numeric(df[call_chg_col], errors="coerce").fillna(0)
        df[put_chg_col] = pd.to_numeric(df[put_chg_col], errors="coerce").fillna(0)
        call_added = float(df[call_chg_col].sum())
        put_added = float(df[put_chg_col].sum())
        if put_added > call_added * 1.3 and put_added > 0:
            oi_behaviour = "longBuildup"  # put writers confident → bullish
        elif call_added > put_added * 1.3 and call_added > 0:
            oi_behaviour = "shortBuildup"  # call writers confident → bearish
        elif put_added < 0 and call_added > 0:
            oi_behaviour = "shortBuildup"
        elif call_added < 0 and put_added > 0:
            oi_behaviour = "longBuildup"
        else:
            oi_behaviour = "neutral"

    return {
        "symbol": sym,
        "pcr": pcr,
        "total_call_oi": int(total_ce),
        "total_put_oi": int(total_pe),
        "top_call_oi_strikes": top_calls,
        "top_put_oi_strikes": top_puts,
        "max_pain": max_pain_strike,
        "atm_iv": atm_iv,
        "oi_behaviour": oi_behaviour,
        "fetched_at_utc": datetime.utcnow().isoformat() + "Z",
    }


def fetch_india_vix() -> dict:
    """Latest India VIX value and percent change."""
    if not NSE_AVAILABLE:
        raise RuntimeError(f"nselib not available: {_IMPORT_ERROR}")
    df = capital_market.india_vix_data(period="1W")
    if df is None or len(df) < 1:
        raise RuntimeError("VIX data empty")
    # Latest row
    latest = df.iloc[-1]
    # column names vary — find a close/value column
    val = None
    chg = None
    for col in df.columns:
        cu = str(col).upper()
        if "CLOSE" in cu and val is None:
            val = float(pd.to_numeric(latest[col], errors="coerce") or 0)
        if "PREV" in cu and "CLOSE" in cu and len(df) >= 2:
            prev = float(pd.to_numeric(df.iloc[-2][col] if col in df.iloc[-2] else 0, errors="coerce") or 0)
    if val is None:
        # try any numeric column
        for col in df.columns:
            v = pd.to_numeric(latest[col], errors="coerce")
            if pd.notna(v) and 5 < float(v) < 100:
                val = float(v)
                break
    if val is None:
        raise RuntimeError("could not parse VIX value")
    # change %
    if len(df) >= 2:
        for col in df.columns:
            if "CLOSE" in str(col).upper():
                prev = float(pd.to_numeric(df.iloc[-2][col], errors="coerce") or 0)
                if prev:
                    chg = round((val - prev) / prev * 100, 2)
                break
    return {
        "vix": round(val, 2),
        "vix_change_pct": chg,
        "fetched_at_utc": datetime.utcnow().isoformat() + "Z",
    }


def fetch_fii_dii_cash() -> dict:
    """Latest FII and DII net cash flow (₹ Cr)."""
    if not NSE_AVAILABLE:
        raise RuntimeError(f"nselib not available: {_IMPORT_ERROR}")
    df = capital_market.fii_dii_trading_activity()
    if df is None or len(df) == 0:
        raise RuntimeError("FII/DII data empty")
    # df typically has columns like: Date, Category, BuyValue, SellValue, NetValue
    # find columns flexibly
    cat_col = _find_col(df, "CATEGORY") or "category"
    net_col = _find_col(df, "NET") or _find_col(df, "NETVALUE")
    if not net_col:
        return {"error": "net column not found", "cols": [str(c) for c in df.columns]}
    out = {"fii_cash": None, "dii_cash": None}
    for _, row in df.iterrows():
        cat = str(row.get(cat_col, "")).upper()
        net = pd.to_numeric(row.get(net_col), errors="coerce")
        if pd.isna(net):
            continue
        if "FII" in cat or "FPI" in cat:
            out["fii_cash"] = round(float(net), 2)
        elif "DII" in cat:
            out["dii_cash"] = round(float(net), 2)
    out["fetched_at_utc"] = datetime.utcnow().isoformat() + "Z"
    return out


def fetch_participant_oi(trade_date: str | None = None) -> dict:
    """Participant-wise OI: returns Long%/Short% for FII, Pro, Client in Index Futures."""
    if not NSE_AVAILABLE:
        raise RuntimeError(f"nselib not available: {_IMPORT_ERROR}")

    if not trade_date:
        # Default: previous trading day in dd-mm-yyyy
        d = datetime.now()
        # Walk back to find a weekday
        for _ in range(7):
            d -= timedelta(days=1)
            if d.weekday() < 5:
                break
        trade_date = d.strftime("%d-%m-%Y")

    try:
        df = derivatives.fii_derivatives_statistics(trade_date=trade_date)
    except Exception:
        df = None

    if df is None or len(df) == 0:
        # Try participant_wise_open_interest if available in this version of nselib
        try:
            df = derivatives.participant_wise_open_interest(trade_date=trade_date)  # type: ignore[attr-defined]
        except Exception:
            df = None

    if df is None or len(df) == 0:
        raise RuntimeError(f"no participant OI for {trade_date}")

    # Find columns. NSE participant-wise file has rows per category (FII, DII, Pro, Client)
    # × instrument type (Index Futures, Index Options, Stock Futures, Stock Options).
    cols_up = {str(c).upper(): c for c in df.columns}
    out: dict[str, Any] = {"trade_date": trade_date, "fetched_at_utc": datetime.utcnow().isoformat() + "Z"}

    # Heuristic: derive long%/short% for FII Index Futures by summing
    # long + short contracts where instrument indicates futures.
    # Different report formats — try to detect.
    cat_col = next((cols_up[k] for k in cols_up if "CLIENT" in k or "CATEGORY" in k or "TYPE" in k), None)
    long_col = next((cols_up[k] for k in cols_up if "LONG" in k and "%" not in k and "PERCENT" not in k), None)
    short_col = next((cols_up[k] for k in cols_up if "SHORT" in k and "%" not in k and "PERCENT" not in k), None)
    instr_col = next((cols_up[k] for k in cols_up if "FUTURE" in k or "OPTION" in k or "INSTRUMENT" in k), None)

    # If we can't tell the schema, return raw row preview to help debugging
    if not (cat_col and long_col and short_col):
        return {
            **out,
            "raw_preview": df.head(8).to_dict(orient="records"),
            "available_columns": [str(c) for c in df.columns],
            "hint": "schema detection failed - use raw_preview to map fields",
        }

    df[long_col] = pd.to_numeric(df[long_col], errors="coerce").fillna(0)
    df[short_col] = pd.to_numeric(df[short_col], errors="coerce").fillna(0)

    for category in ["FII", "FPI", "DII", "Pro", "Client"]:
        # Filter rows containing this category in cat_col
        mask = df[cat_col].astype(str).str.upper().str.contains(category.upper())
        if not mask.any():
            continue
        sub = df[mask]
        # Prefer "Index Futures" rows if instrument column exists
        if instr_col:
            fut_mask = sub[instr_col].astype(str).str.contains("INDEX", case=False) & \
                       sub[instr_col].astype(str).str.contains("FUT", case=False)
            if fut_mask.any():
                sub = sub[fut_mask]
        long_sum = float(sub[long_col].sum())
        short_sum = float(sub[short_col].sum())
        total = long_sum + short_sum
        if total > 0:
            key = category.lower()
            out[f"{key}_long_pct"] = round(long_sum / total * 100, 2)
            out[f"{key}_short_pct"] = round(short_sum / total * 100, 2)
    return out


def fetch_spot(symbol: str) -> dict:
    """Latest spot value for NIFTY 50 or BANK NIFTY."""
    if not NSE_AVAILABLE:
        raise RuntimeError(f"nselib not available: {_IMPORT_ERROR}")
    sym = symbol.upper()
    try:
        from nselib import indices  # type: ignore[attr-defined]
        df = indices.live_index_performances()
        target = "NIFTY 50" if sym == "NIFTY" else "NIFTY BANK"
        for _, row in df.iterrows():
            name = str(row.get("indexName", row.get("Index", "")))
            if target.upper() in name.upper():
                ltp = pd.to_numeric(row.get("last", row.get("LTP", row.get("Last"))), errors="coerce")
                prev = pd.to_numeric(row.get("previousClose", row.get("Previous_Close")), errors="coerce")
                if pd.notna(ltp):
                    return {
                        "symbol": sym,
                        "spot": float(ltp),
                        "prev_close": float(prev) if pd.notna(prev) else None,
                        "fetched_at_utc": datetime.utcnow().isoformat() + "Z",
                    }
    except Exception as exc:
        log.warning("spot via indices module failed: %s", exc)
    raise RuntimeError("could not fetch spot")


# ============================================================
# COMBINED SNAPSHOT
# ============================================================
def snapshot(symbol: str) -> dict:
    """One-shot snapshot used by the dashboard. Partial failures are reported per-field."""
    sym = symbol.upper()
    if sym not in ("NIFTY", "BANKNIFTY"):
        raise ValueError("symbol must be NIFTY or BANKNIFTY")

    result: dict[str, Any] = {"symbol": sym, "fetched_at_utc": datetime.utcnow().isoformat() + "Z", "errors": {}}

    # Option chain - 3 min TTL
    try:
        data, was_cached = _cached(f"oc_{sym}", 180, lambda: fetch_option_chain_summary(sym))
        result["option_chain"] = data
        result["option_chain"]["cached"] = was_cached
    except Exception as exc:
        result["errors"]["option_chain"] = str(exc)

    # VIX - 3 min TTL
    try:
        data, was_cached = _cached("vix", 180, fetch_india_vix)
        result["vix"] = data
        result["vix"]["cached"] = was_cached
    except Exception as exc:
        result["errors"]["vix"] = str(exc)

    # FII/DII cash - 30 min TTL (EOD data)
    try:
        data, was_cached = _cached("fii_dii_cash", 1800, fetch_fii_dii_cash)
        result["fii_dii_cash"] = data
        result["fii_dii_cash"]["cached"] = was_cached
    except Exception as exc:
        result["errors"]["fii_dii_cash"] = str(exc)

    # Participant OI - 1 hour TTL (EOD)
    try:
        data, was_cached = _cached("participant_oi", 3600, lambda: fetch_participant_oi())
        result["participant_oi"] = data
        result["participant_oi"]["cached"] = was_cached
    except Exception as exc:
        result["errors"]["participant_oi"] = str(exc)

    # Spot - 1 min TTL
    try:
        data, was_cached = _cached(f"spot_{sym}", 60, lambda: fetch_spot(sym))
        result["spot"] = data
        result["spot"]["cached"] = was_cached
    except Exception as exc:
        result["errors"]["spot"] = str(exc)

    return result
