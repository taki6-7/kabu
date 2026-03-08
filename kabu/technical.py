"""
テクニカル分析スコアリングモジュール（60点満点）

シグナル内訳:
  - トレンド    15点: パーフェクトオーダー（5MA > 25MA > 75MA）
  - モメンタム  15点: RSI、MACDゴールデンクロス
  - 出来高      15点: 直近出来高が20日平均の1.5倍以上
  - 値動き      15点: 前日陽線、高値水準、ボラティリティ
"""

import logging
import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


def _safe_last(series: pd.Series, default=0.0):
    """シリーズの最終値を安全に取得"""
    if series is None or len(series) == 0:
        return default
    val = series.iloc[-1]
    return float(val) if pd.notna(val) else default


def _calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _calc_macd(close: pd.Series):
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    return macd, signal


def score_trend(close: pd.Series) -> tuple[float, str]:
    """トレンドスコア（15点）"""
    if len(close) < 75:
        return 0.0, "データ不足"

    ma5 = close.rolling(5).mean()
    ma25 = close.rolling(25).mean()
    ma75 = close.rolling(75).mean()

    v5, v25, v75 = _safe_last(ma5), _safe_last(ma25), _safe_last(ma75)

    if v5 > v25 > v75:
        return 15.0, f"パーフェクトオーダー (5MA:{v5:.0f} > 25MA:{v25:.0f} > 75MA:{v75:.0f})"
    elif v5 > v25:
        return 8.0, f"短期上昇トレンド (5MA:{v5:.0f} > 25MA:{v25:.0f})"
    elif v25 > v75:
        return 4.0, f"中期上昇トレンド (25MA:{v25:.0f} > 75MA:{v75:.0f})"
    else:
        return 0.0, f"下降トレンド (5MA:{v5:.0f}, 25MA:{v25:.0f}, 75MA:{v75:.0f})"


def score_momentum(close: pd.Series) -> tuple[float, str]:
    """モメンタムスコア（15点）"""
    if len(close) < 30:
        return 0.0, "データ不足"

    rsi = _calc_rsi(close)
    macd, signal = _calc_macd(close)

    rsi_val = _safe_last(rsi)
    macd_val = _safe_last(macd)
    signal_val = _safe_last(signal)
    prev_macd = macd.iloc[-2] if len(macd) >= 2 else macd_val
    prev_signal = signal.iloc[-2] if len(signal) >= 2 else signal_val

    score = 0.0
    notes = []

    # RSIスコア（8点）
    if 40 <= rsi_val <= 60:
        score += 8.0
        notes.append(f"RSI適正({rsi_val:.1f})")
    elif 35 <= rsi_val < 40 or 60 < rsi_val <= 65:
        score += 4.0
        notes.append(f"RSIやや適正({rsi_val:.1f})")
    elif rsi_val < 30:
        score += 2.0
        notes.append(f"RSI売られ過ぎ({rsi_val:.1f})")
    else:
        notes.append(f"RSI過熱/低迷({rsi_val:.1f})")

    # MACDゴールデンクロス（7点）
    golden_cross = (prev_macd < prev_signal) and (macd_val > signal_val)
    macd_above = macd_val > signal_val

    if golden_cross:
        score += 7.0
        notes.append("MACDゴールデンクロス")
    elif macd_above:
        score += 3.0
        notes.append("MACD買いゾーン")
    else:
        notes.append("MACDデッドクロス/売りゾーン")

    return score, " / ".join(notes)


def score_volume(volume: pd.Series) -> tuple[float, str]:
    """出来高スコア（15点）"""
    if len(volume) < 20:
        return 0.0, "データ不足"

    avg20 = volume.rolling(20).mean()
    avg_val = _safe_last(avg20)
    last_vol = _safe_last(volume)

    if avg_val == 0:
        return 0.0, "出来高データなし"

    ratio = last_vol / avg_val

    if ratio >= 2.0:
        return 15.0, f"出来高急増({ratio:.1f}倍)"
    elif ratio >= 1.5:
        return 10.0, f"出来高増加({ratio:.1f}倍)"
    elif ratio >= 1.0:
        return 5.0, f"出来高平均並み({ratio:.1f}倍)"
    else:
        return 0.0, f"出来高低調({ratio:.1f}倍)"


def score_price_action(high: pd.Series, low: pd.Series, close: pd.Series, open_: pd.Series) -> tuple[float, str]:
    """値動きスコア（15点）"""
    if len(close) < 52:
        return 0.0, "データ不足"

    # 前日陽線（5点）
    last_open = _safe_last(open_)
    last_close = _safe_last(close)
    is_bullish = last_close > last_open

    # 52週高値からの乖離（5点）
    high52w = high.rolling(252).max().iloc[-1] if len(high) >= 252 else high.max()
    pct_from_high = (last_close / float(high52w) - 1) * 100 if float(high52w) > 0 else -100

    # ATR（ボラティリティ適度さ）（5点）
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    atr_pct = (_safe_last(atr) / last_close * 100) if last_close > 0 else 0

    score = 0.0
    notes = []

    if is_bullish:
        score += 5.0
        notes.append("陽線")
    else:
        notes.append("陰線")

    if pct_from_high >= -5:
        score += 5.0
        notes.append(f"高値近辺({pct_from_high:.1f}%)")
    elif pct_from_high >= -15:
        score += 2.0
        notes.append(f"高値からやや下({pct_from_high:.1f}%)")
    else:
        notes.append(f"高値から乖離({pct_from_high:.1f}%)")

    if 1.0 <= atr_pct <= 3.0:
        score += 5.0
        notes.append(f"ボラ適度(ATR:{atr_pct:.1f}%)")
    elif atr_pct < 1.0:
        score += 2.0
        notes.append(f"ボラ低め(ATR:{atr_pct:.1f}%)")
    else:
        notes.append(f"ボラ高め(ATR:{atr_pct:.1f}%)")

    return score, " / ".join(notes)


def calc_technical_score(ticker: str) -> dict:
    """
    銘柄のテクニカルスコアを計算する（60点満点）

    Returns:
        {
            "ticker": str,
            "total": float,
            "trend": float,
            "momentum": float,
            "volume": float,
            "price_action": float,
            "details": dict,
            "error": str | None,
        }
    """
    result = {
        "ticker": ticker,
        "total": 0.0,
        "trend": 0.0,
        "momentum": 0.0,
        "volume": 0.0,
        "price_action": 0.0,
        "details": {},
        "error": None,
    }

    try:
        df = yf.download(ticker, period="1y", auto_adjust=True, progress=False)

        if df is None or len(df) < 80:
            result["error"] = "データ不足"
            return result

        close = df["Close"].squeeze()
        high = df["High"].squeeze()
        low = df["Low"].squeeze()
        open_ = df["Open"].squeeze()
        volume = df["Volume"].squeeze()

        t_score, t_note = score_trend(close)
        m_score, m_note = score_momentum(close)
        v_score, v_note = score_volume(volume)
        p_score, p_note = score_price_action(high, low, close, open_)

        result.update({
            "total": t_score + m_score + v_score + p_score,
            "trend": t_score,
            "momentum": m_score,
            "volume": v_score,
            "price_action": p_score,
            "details": {
                "trend": t_note,
                "momentum": m_note,
                "volume": v_note,
                "price_action": p_note,
                "last_close": _safe_last(close),
            },
        })

    except Exception as e:
        result["error"] = str(e)
        logger.warning(f"{ticker} テクニカルスコア計算エラー: {e}")

    return result
