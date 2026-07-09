#!/usr/bin/env python3
"""Fetch real TDX 龙虎榜 data and real unadjusted daily close prices.

Rules:
- No mock data, no estimated prices, no back-calculated close prices.
- 龙虎榜 rows come from 通达信 TDX endpoint.
- Closing prices come from 东方财富日K线, fqt=0（不复权真实收盘价）.
- If a price cannot be confirmed for the exact 龙虎榜 date, it is written as null
  with price_confirmed=false, and the front end must show it as 未确认.
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib import error, parse, request

BASE_URL = "http://page.tdx.com.cn:7615/TQLEX?Entry=CWServ."
KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
QUOTE_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DAILY_DIR = DATA_DIR / "daily"
CHINA_TZ = timezone(timedelta(hours=8))

PRESELECT_LABELS = ("机构专用", "深股通专用", "沪股通专用")
HOLDING_DAYS = (1, 3, 5, 10)
MAX_STEADY_PCT_CHANGE = 7.0
UPDATE_NOT_BEFORE_HOUR = 18
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def now_cn() -> datetime:
    return datetime.now(CHINA_TZ)


def read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def number(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("%", "")
    if text in {"", "-", "--", "None", "null"}:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def maybe_number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("%", "")
    if text in {"", "-", "--", "None", "null"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def post_json(url: str, body: dict[str, Any], timeout: int = 20, attempts: int = 4) -> dict[str, Any]:
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    last_error: Exception | None = None
    for attempt in range(attempts):
        req = request.Request(
            url,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "text/plain; charset=utf-8",
                "Accept": "application/json,text/plain,*/*",
                "User-Agent": USER_AGENT,
                "Connection": "close",
            },
        )
        try:
            with request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception as exc:
            last_error = exc
            time.sleep(0.8 + attempt * 1.2)
    raise RuntimeError(f"POST failed after retries: {last_error}")


def get_json(url: str, timeout: int = 20, attempts: int = 5) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(attempts):
        cache_buster = int(time.time() * 1000)
        sep = "&" if "?" in url else "?"
        req = request.Request(
            f"{url}{sep}_={cache_buster}",
            headers={
                "Accept": "application/json,text/plain,*/*",
                "Referer": "https://quote.eastmoney.com/",
                "User-Agent": USER_AGENT,
                "Connection": "close",
            },
        )
        try:
            with request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace").strip()
            # Eastmoney normally returns JSON. This also tolerates jsonp wrappers.
            if raw.startswith("{"):
                return json.loads(raw)
            start = raw.find("{")
            end = raw.rfind("}")
            if start >= 0 and end > start:
                return json.loads(raw[start : end + 1])
            raise RuntimeError(raw[:160])
        except (error.URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
            last_error = exc
            time.sleep(0.8 + attempt * 1.2)
    raise RuntimeError(f"GET failed after retries: {last_error}")


def call_tdx(entry: str, params: list[Any]) -> dict[str, Any]:
    return post_json(BASE_URL + entry, {"Params": params})


def table_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result_sets = payload.get("ResultSets") or []
    if not result_sets:
        return []
    result = result_sets[0]
    columns = result.get("ColName") or [col.get("Name") for col in result.get("ColDes", [])]
    rows: list[dict[str, Any]] = []
    for row in result.get("Content", []):
        rows.append({str(columns[i]): row[i] if i < len(row) else None for i in range(len(columns))})
    return rows


def latest_date() -> str:
    rows = table_rows(call_tdx("cfg_fx_yzlhb", ["rq", "", "", "", "", 0, 30]))
    if not rows or not rows[0].get("rq"):
        raise RuntimeError("TDX latest-date endpoint returned no date.")
    return str(rows[0]["rq"]).split(" ")[0]


def secid_for_stock(code: str, market: Any = "") -> str:
    code = str(code or "").strip()
    market_text = str(market or "").strip()
    if market_text in {"0", "1"}:
        return f"{market_text}.{code}"
    if code.startswith(("6", "9")) and not code.startswith("920"):
        return f"1.{code}"
    return f"0.{code}"


def price_unconfirmed(message: str, source: str = "eastmoney_daily_kline") -> dict[str, Any]:
    return {
        "price_date": "",
        "open": None,
        "close": None,
        "high": None,
        "low": None,
        "volume": None,
        "amount": None,
        "amplitude_pct": None,
        "pct_change": None,
        "change_amount": None,
        "turnover_pct": None,
        "price_source": source,
        "price_basis": "unadjusted_daily_close_fqt0",
        "price_confirmed": False,
        "price_error": message[:180],
    }


def clean_price(price: dict[str, Any], expected_date: str, source: str) -> dict[str, Any] | None:
    close = maybe_number(price.get("close"))
    date_value = str(price.get("price_date") or "")
    if date_value != expected_date or close is None or close <= 0:
        return None
    return {
        "price_date": expected_date,
        "open": maybe_number(price.get("open")),
        "close": close,
        "high": maybe_number(price.get("high")),
        "low": maybe_number(price.get("low")),
        "volume": maybe_number(price.get("volume")),
        "amount": maybe_number(price.get("amount")),
        "amplitude_pct": maybe_number(price.get("amplitude_pct")),
        "pct_change": maybe_number(price.get("pct_change")),
        "change_amount": maybe_number(price.get("change_amount")),
        "turnover_pct": maybe_number(price.get("turnover_pct")),
        "price_source": source,
        "price_basis": "unadjusted_daily_close_fqt0",
        "price_confirmed": True,
    }


def fetch_batch_quotes(raw_rows: list[dict[str, Any]], date_value: str) -> dict[str, dict[str, Any]]:
    """Use current quote only when its quote date exactly equals the LHB date."""
    wanted = date_value.replace("-", "")
    secids = sorted({secid_for_stock(row.get("gpdm"), row.get("sc")) for row in raw_rows if row.get("gpdm")})
    prices: dict[str, dict[str, Any]] = {}
    for start in range(0, len(secids), 80):
        chunk = ",".join(secids[start : start + 80])
        if not chunk:
            continue
        params = {
            "fltt": "2",
            "fields": "f12,f14,f2,f3,f4,f5,f6,f8,f15,f16,f17,f18,f297",
            "secids": chunk,
        }
        url = f"{QUOTE_URL}?{parse.urlencode(params)}"
        try:
            payload = get_json(url, timeout=15, attempts=3)
        except Exception:
            continue
        for item in ((payload.get("data") or {}).get("diff") or []):
            code = str(item.get("f12") or "").strip()
            if not code or str(item.get("f297") or "") != wanted:
                continue
            price = clean_price(
                {
                    "price_date": date_value,
                    "open": item.get("f17"),
                    "close": item.get("f2"),
                    "high": item.get("f15"),
                    "low": item.get("f16"),
                    "volume": item.get("f5"),
                    "amount": item.get("f6"),
                    "pct_change": item.get("f3"),
                    "change_amount": item.get("f4"),
                    "turnover_pct": item.get("f8"),
                },
                date_value,
                "eastmoney_batch_quote_exact_date",
            )
            if price:
                prices[code] = price
        time.sleep(0.2)
    return prices


def fetch_daily_kline(code: str, date_value: str, market: Any = "") -> dict[str, Any] | None:
    day = date_value.replace("-", "")
    params = {
        "secid": secid_for_stock(code, market),
        "klt": "101",
        "fqt": "0",  # 0=不复权；真实收盘价不能用前复权/后复权
        "beg": day,
        "end": day,
        "lmt": "1",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    }
    payload = get_json(f"{KLINE_URL}?{parse.urlencode(params)}", timeout=18, attempts=5)
    klines = ((payload.get("data") or {}).get("klines") or [])
    if not klines:
        return None
    parts = klines[0].split(",")
    if len(parts) < 11 or parts[0] != date_value:
        return None
    return clean_price(
        {
            "price_date": parts[0],
            "open": parts[1],
            "close": parts[2],
            "high": parts[3],
            "low": parts[4],
            "volume": parts[5],
            "amount": parts[6],
            "amplitude_pct": parts[7],
            "pct_change": parts[8],
            "change_amount": parts[9],
            "turnover_pct": parts[10],
        },
        date_value,
        "eastmoney_daily_kline_fqt0",
    )


def fetch_closing_prices(raw_rows: list[dict[str, Any]], date_value: str) -> dict[str, dict[str, Any]]:
    grouped_market: dict[str, Any] = {}
    for row in raw_rows:
        code = str(row.get("gpdm") or "").strip()
        if code and code not in grouped_market:
            grouped_market[code] = row.get("sc")

    prices = fetch_batch_quotes(raw_rows, date_value)
    for code, market in sorted(grouped_market.items()):
        if code in prices:
            continue
        try:
            price = fetch_daily_kline(code, date_value, market)
        except Exception as exc:
            price = price_unconfirmed(str(exc))
        if price is None:
            price = price_unconfirmed("No exact unadjusted daily K-line for the LHB date.")
        prices[code] = price
        time.sleep(0.25)
    return prices


def normalize_rows(raw_rows: list[dict[str, Any]], date_value: str, prices: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in raw_rows:
        code = str(row.get("gpdm") or "").strip()
        mrje = number(row.get("mrje"))
        mcje = number(row.get("mcje"))
        normalized.append(
            {
                "rq": str(row.get("rq") or date_value).split(" ")[0],
                "yzmc": str(row.get("yzmc") or ""),
                "yyb": str(row.get("yyb") or ""),
                "gpdm": code,
                "gpmc": str(row.get("gpmc") or ""),
                "sc": str(row.get("sc") or ""),
                "sblx": str(row.get("sblx") or ""),
                "mrje": mrje,
                "mcje": mcje,
                "jmr": mrje - mcje,
                "price": prices.get(code) or price_unconfirmed("Stock code missing from price map."),
            }
        )
    return normalized


def summarize(rows: list[dict[str, Any]], date_value: str) -> dict[str, Any]:
    yz = Counter()
    yyb = Counter()
    stocks = defaultdict(lambda: {"gpdm": "", "gpmc": "", "mrje": 0.0, "mcje": 0.0, "jmr": 0.0, "count": 0, "price": {}})
    yz_money = defaultdict(lambda: {"name": "", "mrje": 0.0, "mcje": 0.0, "jmr": 0.0, "count": 0})
    yyb_money = defaultdict(lambda: {"name": "", "mrje": 0.0, "mcje": 0.0, "jmr": 0.0, "count": 0})

    for row in rows:
        yz_name = row["yzmc"]
        yyb_name = row["yyb"]
        stock_key = f"{row['gpdm']}|{row['gpmc']}"
        if yz_name:
            yz[yz_name] += 1
            yz_money[yz_name]["name"] = yz_name
            yz_money[yz_name]["count"] += 1
            yz_money[yz_name]["mrje"] += row["mrje"]
            yz_money[yz_name]["mcje"] += row["mcje"]
            yz_money[yz_name]["jmr"] += row["jmr"]
        if yyb_name:
            yyb[yyb_name] += 1
            yyb_money[yyb_name]["name"] = yyb_name
            yyb_money[yyb_name]["count"] += 1
            yyb_money[yyb_name]["mrje"] += row["mrje"]
            yyb_money[yyb_name]["mcje"] += row["mcje"]
            yyb_money[yyb_name]["jmr"] += row["jmr"]
        stocks[stock_key]["gpdm"] = row["gpdm"]
        stocks[stock_key]["gpmc"] = row["gpmc"]
        stocks[stock_key]["price"] = row["price"]
        stocks[stock_key]["count"] += 1
        stocks[stock_key]["mrje"] += row["mrje"]
        stocks[stock_key]["mcje"] += row["mcje"]
        stocks[stock_key]["jmr"] += row["jmr"]

    confirmed = sum(1 for row in rows if row["price"].get("price_confirmed"))
    return {
        "date": date_value,
        "updatedAt": now_cn().isoformat(timespec="seconds"),
        "rowCount": len(rows),
        "activeYzCount": len(yz),
        "activeYybCount": len(yyb),
        "topYzByCount": yz.most_common(20),
        "topYybByCount": yyb.most_common(20),
        "topYzByNetBuy": sorted(yz_money.values(), key=lambda item: item["jmr"], reverse=True)[:20],
        "topYybByNetBuy": sorted(yyb_money.values(), key=lambda item: item["jmr"], reverse=True)[:20],
        "topStocksByNetBuy": sorted(stocks.values(), key=lambda item: item["jmr"], reverse=True)[:20],
        "topStocksByNetSell": sorted(stocks.values(), key=lambda item: item["jmr"])[:20],
        "pricePolicy": {
            "rule": "只写入与龙虎榜日期一致的东方财富不复权日K真实收盘价；不估算、不倒推、不虚标。",
            "lhbSource": "tdx_cfg_fx_yzlhb",
            "priceSource": "eastmoney_daily_kline",
            "priceBasis": "fqt=0 不复权真实收盘价",
            "confirmedRows": confirmed,
            "unconfirmedRows": len(rows) - confirmed,
        },
    }


def matched_preselect_labels(row: dict[str, Any]) -> list[str]:
    text = f"{row.get('yzmc', '')},{row.get('yyb', '')}"
    return [label for label in PRESELECT_LABELS if label in text]


def risk_score(item: dict[str, Any]) -> float:
    pct = item.get("pctChange")
    turnover = item.get("turnoverPct")
    score = 0.0
    if pct is None:
        score += 40
    else:
        score += max(0.0, pct) * 3
        if pct > MAX_STEADY_PCT_CHANGE:
            score += 30
        if pct < -5:
            score += 8
    if turnover is not None and turnover > 25:
        score += 15
    if item.get("jmr", 0) < 0:
        score += 30
    score += min(item.get("count", 0), 10) * 0.6
    return score


def is_steady_candidate(item: dict[str, Any]) -> bool:
    return bool(
        item.get("entryPriceConfirmed")
        and item.get("jmr", 0) > 0
        and item.get("pctChange") is not None
        and item.get("pctChange") <= MAX_STEADY_PCT_CHANGE
    )


def max_risk(current: str, candidate: str) -> str:
    order = {"低": 0, "中": 1, "高": 2}
    return candidate if order[candidate] > order[current] else current


def assess_risk(item: dict[str, Any]) -> dict[str, Any]:
    pct = item.get("pctChange")
    turnover = item.get("turnoverPct")
    risks: list[str] = []
    level = "低"
    chase_risk = "低"
    if not item.get("entryPriceConfirmed"):
        level = "高"
        chase_risk = "高"
        risks.append("入选日真实收盘价未确认，不能计算可靠入场基准。")
    if pct is not None and pct > MAX_STEADY_PCT_CHANGE:
        level = "高"
        chase_risk = "高"
        risks.append(f"入选日涨幅 {pct:.2f}%，超过稳健型不追高阈值 {MAX_STEADY_PCT_CHANGE:.2f}%。")
    elif pct is not None and pct > 3:
        level = max_risk(level, "中")
        chase_risk = "中"
        risks.append(f"入选日涨幅 {pct:.2f}%，次日若继续冲高容易追高。")
    if turnover is not None and turnover > 25:
        level = max_risk(level, "中")
        risks.append(f"换手率 {turnover:.2f}%，短线分歧和波动可能偏大。")
    if item.get("jmr", 0) <= 0:
        level = "高"
        risks.append("机构/沪深股通相关净买入为非正值，不符合稳健型净流入观察条件。")
    if not risks:
        risks.append("风险相对较低，但仍需等待次日回踩承接确认，不直接追价。")
    return {"level": level, "chaseRisk": chase_risk, "score": round(risk_score(item), 2), "notes": risks}


def pending_return(reason: str) -> dict[str, Any]:
    return {"targetDate": "", "close": None, "returnPct": None, "win": None, "confirmed": False, "reason": reason}


def build_today_preselect(rows: list[dict[str, Any]], date_value: str, limit: int = 10) -> list[dict[str, Any]]:
    grouped = defaultdict(
        lambda: {
            "date": date_value,
            "gpdm": "",
            "gpmc": "",
            "labels": set(),
            "mrje": 0.0,
            "mcje": 0.0,
            "jmr": 0.0,
            "count": 0,
            "entryPrice": None,
            "entryPriceConfirmed": False,
            "pctChange": None,
            "turnoverPct": None,
        }
    )
    for row in rows:
        labels = matched_preselect_labels(row)
        if not labels:
            continue
        key = row["gpdm"]
        item = grouped[key]
        item["gpdm"] = row["gpdm"]
        item["gpmc"] = row["gpmc"]
        item["labels"].update(labels)
        item["mrje"] += row["mrje"]
        item["mcje"] += row["mcje"]
        item["jmr"] += row["jmr"]
        item["count"] += 1
        price = row.get("price") or {}
        if price.get("price_confirmed") and item["entryPrice"] is None:
            item["entryPrice"] = price.get("close")
            item["entryPriceConfirmed"] = True
            item["pctChange"] = price.get("pct_change")
            item["turnoverPct"] = price.get("turnover_pct")
    candidates = [item for item in grouped.values() if is_steady_candidate(item)]
    if len(candidates) < limit:
        backups = [item for item in grouped.values() if item not in candidates and item.get("entryPriceConfirmed")]
        backups.sort(key=lambda item: risk_score(item))
        candidates.extend(backups[: max(0, limit - len(candidates))])
    picks = sorted(candidates, key=lambda item: (risk_score(item), -item["jmr"], -item["count"]))[:limit]
    out: list[dict[str, Any]] = []
    for item in picks:
        item["labels"] = sorted(item["labels"])
        item["source"] = "机构专用/深股通专用/沪股通专用稳健型预选"
        item["strategyType"] = "稳健型低吸观察，不追高"
        item["buyDiscipline"] = "入选后只作为次日低吸观察；不在大幅冲高时追买，优先等待回踩、承接和量能确认。"
        item["riskAssessment"] = assess_risk(item)
        item["holdingReturns"] = {f"T+{day}": pending_return("waiting for future close") for day in HOLDING_DAYS}
        out.append(item)
    return out


def fetch_kline_series(code: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
    params = {
        "secid": secid_for_stock(code),
        "klt": "101",
        "fqt": "0",
        "beg": start_date.replace("-", ""),
        "end": end_date.replace("-", ""),
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
    }
    payload = get_json(f"{KLINE_URL}?{parse.urlencode(params)}", timeout=20, attempts=5)
    series: list[dict[str, Any]] = []
    for line in ((payload.get("data") or {}).get("klines") or []):
        parts = line.split(",")
        if len(parts) < 11:
            continue
        close = maybe_number(parts[2])
        if close is None:
            continue
        series.append({"date": parts[0], "open": maybe_number(parts[1]), "close": close, "high": maybe_number(parts[3]), "low": maybe_number(parts[4]), "pct_change": maybe_number(parts[8])})
    return series


def update_holding_returns(entry: dict[str, Any], latest_date_value: str) -> None:
    entry.setdefault("holdingReturns", {f"T+{day}": pending_return("waiting for future close") for day in HOLDING_DAYS})
    entry_price = entry.get("entryPrice")
    if not entry.get("entryPriceConfirmed") or not entry_price:
        for day in HOLDING_DAYS:
            entry["holdingReturns"][f"T+{day}"] = pending_return("entry close is not confirmed")
        return
    try:
        series = fetch_kline_series(entry["gpdm"], entry["date"], latest_date_value)
    except Exception as exc:
        for day in HOLDING_DAYS:
            key = f"T+{day}"
            if not entry["holdingReturns"].get(key, {}).get("confirmed"):
                entry["holdingReturns"][key] = pending_return(str(exc)[:160])
        return
    dates = [item["date"] for item in series]
    if entry["date"] not in dates:
        for day in HOLDING_DAYS:
            entry["holdingReturns"][f"T+{day}"] = pending_return("entry date missing in daily kline")
        return
    entry_index = dates.index(entry["date"])
    for day in HOLDING_DAYS:
        key = f"T+{day}"
        target_index = entry_index + day
        if target_index >= len(series):
            if not entry["holdingReturns"].get(key, {}).get("confirmed"):
                entry["holdingReturns"][key] = pending_return("waiting for future close")
            continue
        target = series[target_index]
        return_pct = (target["close"] / entry_price - 1) * 100
        entry["holdingReturns"][key] = {"targetDate": target["date"], "close": target["close"], "returnPct": round(return_pct, 4), "win": return_pct > 0, "confirmed": True, "reason": ""}


def summarize_preselect(entries: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {"total": len(entries), "milestones": {}}
    for day in HOLDING_DAYS:
        key = f"T+{day}"
        confirmed = [item["holdingReturns"].get(key) for item in entries if item.get("holdingReturns", {}).get(key, {}).get("confirmed")]
        wins = [item for item in confirmed if item.get("win")]
        avg_return = sum(item["returnPct"] for item in confirmed) / len(confirmed) if confirmed else None
        summary["milestones"][key] = {"confirmedCount": len(confirmed), "winCount": len(wins), "lossCount": len(confirmed) - len(wins), "winRate": round(len(wins) / len(confirmed) * 100, 2) if confirmed else None, "avgReturnPct": round(avg_return, 4) if avg_return is not None else None}
    return summary


def update_preselect_pool(rows: list[dict[str, Any]], date_value: str) -> dict[str, Any]:
    path = DATA_DIR / "preselect-pool.json"
    pool = read_json(path, {"updatedAt": "", "entries": [], "summary": {}})
    entries = pool.get("entries", [])
    seen = {(item.get("date"), item.get("gpdm")) for item in entries}
    for pick in build_today_preselect(rows, date_value):
        key = (pick["date"], pick["gpdm"])
        if key not in seen:
            entries.append(pick)
            seen.add(key)
    entries = sorted(entries, key=lambda item: (item.get("date", ""), item.get("jmr", 0)), reverse=True)[:260]
    for item in entries:
        update_holding_returns(item, date_value)
        time.sleep(0.10)
    pool = {
        "updatedAt": now_cn().isoformat(timespec="seconds"),
        "rule": "只从机构专用、深股通专用、沪股通专用相关记录中，按当日净买入排序生成稳健型预选池；收益率全部用不复权真实日K收盘价计算。",
        "holdingDays": list(HOLDING_DAYS),
        "entries": entries,
        "summary": summarize_preselect(entries),
    }
    write_json(path, pool)
    return pool


def ensure_after_close_update_window() -> None:
    if os.getenv("FORCE_UPDATE") == "1":
        return
    current = now_cn()
    if current.hour < UPDATE_NOT_BEFORE_HOUR:
        raise SystemExit(f"Skip update: Beijing time {current.isoformat(timespec='seconds')}; allowed after {UPDATE_NOT_BEFORE_HOUR}:00.")


def should_skip_no_new_trading_day(date_value: str) -> bool:
    if os.getenv("FORCE_UPDATE") == "1":
        return False
    today = now_cn().date().isoformat()
    if date_value == today:
        return False
    latest = read_json(DATA_DIR / "latest.json", {})
    summary = latest.get("summary") or {}
    price_policy = summary.get("pricePolicy") or {}
    if summary.get("date") == date_value and price_policy.get("unconfirmedRows", 1) == 0:
        print(f"No new trading day. Latest confirmed data is already {date_value}. Skip writing.")
        return True
    return False


def main() -> int:
    ensure_after_close_update_window()
    date_value = latest_date()
    if should_skip_no_new_trading_day(date_value):
        return 0

    raw_rows = table_rows(call_tdx("cfg_fx_yzlhb", ["yzlhb", date_value, "", "", "", 0, 1000]))
    if not raw_rows:
        raise RuntimeError(f"TDX returned no 龙虎榜 rows for {date_value}.")

    prices = fetch_closing_prices(raw_rows, date_value)
    rows = normalize_rows(raw_rows, date_value, prices)
    summary = summarize(rows, date_value)
    preselect_pool = update_preselect_pool(rows, date_value)
    summary["preselectPool"] = {"entryCount": preselect_pool["summary"].get("total", 0), "summary": preselect_pool["summary"]}

    daily_path = DAILY_DIR / f"{date_value}.json"
    latest_path = DATA_DIR / "latest.json"
    index_path = DATA_DIR / "index.json"
    write_json(daily_path, {"summary": summary, "rows": rows})
    write_json(latest_path, {"summary": summary, "rows": rows})

    index = read_json(index_path, {"dates": [], "summaries": {}})
    dates = [item for item in index.get("dates", []) if item != date_value]
    dates.append(date_value)
    dates.sort(reverse=False)
    index["dates"] = dates[-260:]
    index["latestDate"] = date_value
    index["lastUpdatedAt"] = summary["updatedAt"]
    index.setdefault("summaries", {})[date_value] = {
        "date": date_value,
        "rowCount": summary["rowCount"],
        "activeYzCount": summary["activeYzCount"],
        "activeYybCount": summary["activeYybCount"],
        "confirmedRows": summary["pricePolicy"]["confirmedRows"],
        "unconfirmedRows": summary["pricePolicy"]["unconfirmedRows"],
    }
    write_json(index_path, index)
    print(f"TDX data updated: {date_value}, rows={len(rows)}, prices={summary['pricePolicy']['confirmedRows']}/{len(rows)} confirmed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
