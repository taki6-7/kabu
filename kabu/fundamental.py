"""
ファンダメンタル・ニューススコアリングモジュール（40点満点）

シグナル内訳:
  - IR・適時開示  15点: 前日の開示情報をLLMで評価（未実装時はスキップ）
  - 業績トレンド  10点: 直近2四半期の業績修正履歴
  - 米国市場連動  10点: 関連セクターの前日NY市場動向
  - バリュエーション 5点: PER・PBRが業種平均以下
"""

import logging
import time
from datetime import datetime, timedelta

import yfinance as yf

logger = logging.getLogger(__name__)

# セクター別 米国ETFティッカー（NY連動判定用）
US_SECTOR_ETFS = {
    "Technology": "XLK",
    "Financial Services": "XLF",
    "Healthcare": "XLV",
    "Consumer Cyclical": "XLY",
    "Industrials": "XLI",
    "Energy": "XLE",
    "Materials": "XLB",
    "Real Estate": "XLRE",
    "Utilities": "XLU",
    "Communication Services": "XLC",
    "Consumer Defensive": "XLP",
}

# 業種別PER・PBR参考値（日本株概算）
SECTOR_AVERAGES = {
    "Technology": {"per": 30, "pbr": 3.5},
    "Financial Services": {"per": 12, "pbr": 0.7},
    "Healthcare": {"per": 25, "pbr": 2.5},
    "Consumer Cyclical": {"per": 18, "pbr": 1.5},
    "Industrials": {"per": 16, "pbr": 1.2},
    "Energy": {"per": 10, "pbr": 0.9},
    "Materials": {"per": 14, "pbr": 1.0},
    "Real Estate": {"per": 20, "pbr": 1.5},
    "Utilities": {"per": 14, "pbr": 1.1},
    "Communication Services": {"per": 20, "pbr": 2.0},
    "Consumer Defensive": {"per": 20, "pbr": 2.0},
    "default": {"per": 18, "pbr": 1.5},
}


def _get_us_sector_return(etf_ticker: str) -> float:
    """米国セクターETFの前日リターンを取得（%）"""
    try:
        df = yf.download(etf_ticker, period="5d", auto_adjust=True, progress=False)
        if df is None or len(df) < 2:
            return 0.0
        close = df["Close"].squeeze()
        return float((close.iloc[-1] / close.iloc[-2] - 1) * 100)
    except Exception:
        return 0.0


def score_us_market(sector: str) -> tuple[float, str]:
    """米国市場連動スコア（10点）"""
    etf = US_SECTOR_ETFS.get(sector, "SPY")
    ret = _get_us_sector_return(etf)

    if ret >= 1.5:
        return 10.0, f"米国{sector}セクター強い({ret:+.2f}%)"
    elif ret >= 0.5:
        return 7.0, f"米国{sector}セクターやや強い({ret:+.2f}%)"
    elif ret >= -0.5:
        return 4.0, f"米国{sector}セクター横ばい({ret:+.2f}%)"
    elif ret >= -1.5:
        return 1.0, f"米国{sector}セクターやや弱い({ret:+.2f}%)"
    else:
        return 0.0, f"米国{sector}セクター弱い({ret:+.2f}%)"


def score_valuation(info: dict, sector: str) -> tuple[float, str]:
    """バリュエーションスコア（5点）"""
    avg = SECTOR_AVERAGES.get(sector, SECTOR_AVERAGES["default"])

    per = info.get("trailingPE") or info.get("forwardPE")
    pbr = info.get("priceToBook")

    if per is None and pbr is None:
        return 2.5, "バリュエーションデータなし"

    score = 0.0
    notes = []

    if per is not None:
        if 0 < per < avg["per"]:
            score += 2.5
            notes.append(f"PER割安({per:.1f}x < 平均{avg['per']}x)")
        elif per > 0:
            notes.append(f"PER高め({per:.1f}x)")
        else:
            notes.append("PER赤字")

    if pbr is not None:
        if 0 < pbr < avg["pbr"]:
            score += 2.5
            notes.append(f"PBR割安({pbr:.2f}x < 平均{avg['pbr']}x)")
        elif pbr > 0:
            notes.append(f"PBR高め({pbr:.2f}x)")

    return min(score, 5.0), " / ".join(notes)


def score_earnings_trend(info: dict) -> tuple[float, str]:
    """業績トレンドスコア（10点）"""
    # yfinanceで取得できる情報から業績の方向性を判断
    score = 0.0
    notes = []

    # 売上成長率
    revenue_growth = info.get("revenueGrowth")
    if revenue_growth is not None:
        if revenue_growth >= 0.10:
            score += 4.0
            notes.append(f"売上高成長({revenue_growth*100:.1f}%)")
        elif revenue_growth >= 0.0:
            score += 2.0
            notes.append(f"売上高微増({revenue_growth*100:.1f}%)")
        else:
            notes.append(f"売上高減少({revenue_growth*100:.1f}%)")

    # 利益成長率
    earnings_growth = info.get("earningsGrowth")
    if earnings_growth is not None:
        if earnings_growth >= 0.10:
            score += 4.0
            notes.append(f"利益成長({earnings_growth*100:.1f}%)")
        elif earnings_growth >= 0.0:
            score += 2.0
            notes.append(f"利益微増({earnings_growth*100:.1f}%)")
        else:
            notes.append(f"利益減少({earnings_growth*100:.1f}%)")

    # ROE
    roe = info.get("returnOnEquity")
    if roe is not None and roe >= 0.10:
        score += 2.0
        notes.append(f"ROE良好({roe*100:.1f}%)")

    if not notes:
        return 5.0, "業績データなし（中立評価）"

    return min(score, 10.0), " / ".join(notes)


def calc_fundamental_score(ticker: str) -> dict:
    """
    銘柄のファンダメンタルスコアを計算する（40点満点）
    ※ IR・適時開示スコア（15点）は現状0点として扱い、他3項目（25点）で評価

    Returns:
        {
            "ticker": str,
            "total": float,
            "us_market": float,
            "valuation": float,
            "earnings": float,
            "details": dict,
            "error": str | None,
        }
    """
    result = {
        "ticker": ticker,
        "total": 0.0,
        "ir": 0.0,        # 将来実装
        "us_market": 0.0,
        "valuation": 0.0,
        "earnings": 0.0,
        "details": {},
        "error": None,
        "name": "",
        "sector": "",
    }

    try:
        t = yf.Ticker(ticker)
        info = t.info or {}

        name = info.get("longName") or info.get("shortName") or ticker
        sector = info.get("sector") or "default"

        result["name"] = name
        result["sector"] = sector

        us_score, us_note = score_us_market(sector)
        val_score, val_note = score_valuation(info, sector)
        earn_score, earn_note = score_earnings_trend(info)

        # IR未実装のため25点満点 → 40点満点に正規化
        raw_total = us_score + val_score + earn_score
        normalized_total = raw_total * (40 / 25)

        result.update({
            "total": round(normalized_total, 2),
            "us_market": us_score,
            "valuation": val_score,
            "earnings": earn_score,
            "details": {
                "ir": "（将来実装予定）",
                "us_market": us_note,
                "valuation": val_note,
                "earnings": earn_note,
            },
        })

        time.sleep(0.3)

    except Exception as e:
        result["error"] = str(e)
        logger.warning(f"{ticker} ファンダスコア計算エラー: {e}")

    return result
