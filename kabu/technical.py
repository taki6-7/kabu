"""
テクニカル分析スコアリングモジュール（60点満点）

【日次リバランス・翌日+3%狙い向け設計】

シグナル内訳:
  - 短期モメンタム  15点: 直近1〜5日間のリターン（直近の勢い）
  - モメンタム指標  15点: RSI(7)短期版 + MACD（買われているか）
  - 出来高サージ    15点: 直近出来高 vs 5日平均（機関投資家の買いを捕捉）
  - 引け足強度      15点: 高値引け・5日高値ブレイク（翌日続伸の予兆）
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
    """短期モメンタムスコア（15点）
    直近5日間の価格変動率を評価。翌日続伸を予測する最重要シグナル。
    20日MAより上にいるかで中期トレンド確認ボーナスを付与。
    """
    if len(close) < 10:
        return 0.0, "データ不足"

    last = close.iloc[-1]

    # 直近5日間リターン（メイン評価）
    roc5 = (last / close.iloc[-6] - 1) * 100 if len(close) >= 6 else 0.0
    # 直近3日間リターン（直近加速度）
    roc3 = (last / close.iloc[-4] - 1) * 100 if len(close) >= 4 else 0.0

    # 中期トレンド確認（20日MA以上 = 下落相場での逆張りを避ける）
    ma20 = close.rolling(20).mean()
    above_ma20 = float(last) > _safe_last(ma20) if len(close) >= 20 else True
    bonus = 3.0 if above_ma20 else 0.0

    if roc5 >= 5.0:
        base = 12.0
        label = f"5日急騰({roc5:.1f}%)"
    elif roc5 >= 3.0:
        base = 9.0
        label = f"5日強上昇({roc5:.1f}%)"
    elif roc5 >= 1.0:
        base = 5.0
        label = f"5日上昇({roc5:.1f}%)"
    elif roc5 >= 0.0:
        base = 2.0
        label = f"5日横ばい({roc5:.1f}%)"
    else:
        return 0.0, f"5日下落({roc5:.1f}%)"

    score = min(15.0, base + bonus)
    suffix = "+MA20上" if above_ma20 else ""
    return score, f"{label}{suffix} / 3日:{roc3:.1f}%"


def score_momentum(close: pd.Series) -> tuple[float, str]:
    """モメンタム指標スコア（15点）
    RSI(7)短期版で直近の買い圧力を測定。
    RSI14は遅すぎるため日次狙いには7日を使用。
    """
    if len(close) < 15:
        return 0.0, "データ不足"

    rsi = _calc_rsi(close, period=7)   # 短期RSI(7)に変更
    macd, signal = _calc_macd(close)

    rsi_val = _safe_last(rsi)
    macd_val = _safe_last(macd)
    signal_val = _safe_last(signal)
    prev_macd = macd.iloc[-2] if len(macd) >= 2 else macd_val
    prev_signal = signal.iloc[-2] if len(signal) >= 2 else signal_val

    score = 0.0
    notes = []

    # RSI(7)スコア（8点）- 短期買い圧力を評価
    # RSI(7)は14より反応が速く日次戦略に適している
    # 60-80ゾーン = 短期上昇モメンタム継続中
    if 60 <= rsi_val <= 80:
        score += 8.0
        notes.append(f"RSI(7)上昇圏({rsi_val:.1f})")
    elif 55 <= rsi_val < 60:
        score += 5.0
        notes.append(f"RSI(7)強気({rsi_val:.1f})")
    elif 50 <= rsi_val < 55:
        score += 2.0
        notes.append(f"RSI(7)中立上({rsi_val:.1f})")
    else:
        notes.append(f"RSI(7){'過熱' if rsi_val > 80 else '弱気'}({rsi_val:.1f})")

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
    """出来高サージスコア（15点）
    直近出来高 vs 5日平均で当日の機関投資家の動きを捕捉。
    日次戦略では「今日の出来高が急増しているか」が最重要。
    """
    if len(volume) < 10:
        return 0.0, "データ不足"

    avg5 = volume.rolling(5).mean()
    avg5_val = _safe_last(avg5)
    last_vol = _safe_last(volume)

    if avg5_val == 0:
        return 0.0, "出来高データなし"

    ratio = last_vol / avg5_val  # 当日出来高 / 直近5日平均

    if ratio >= 3.0:
        return 15.0, f"出来高爆発({ratio:.1f}倍/5日比)"
    elif ratio >= 2.0:
        return 12.0, f"出来高急増({ratio:.1f}倍/5日比)"
    elif ratio >= 1.5:
        return 8.0, f"出来高増加({ratio:.1f}倍/5日比)"
    elif ratio >= 1.0:
        return 4.0, f"出来高平均並み({ratio:.1f}倍/5日比)"
    else:
        return 0.0, f"出来高低調({ratio:.1f}倍/5日比)"


def score_price_action(high: pd.Series, low: pd.Series, close: pd.Series, open_: pd.Series) -> tuple[float, str]:
    """引け足強度スコア（15点）
    高値引け（引け値が当日高値付近）は翌日続伸のサインとして有名。
    5日高値ブレイクアウトも翌日の買いを呼び込みやすい。
    """
    if len(close) < 10:
        return 0.0, "データ不足"

    last_close = _safe_last(close)
    last_high  = _safe_last(high)
    last_low   = _safe_last(low)
    last_open  = _safe_last(open_)

    # 引け位置スコア（7点）: 当日の値幅の何%の位置で引けたか
    # 0% = 安値引け, 100% = 高値引け
    day_range = last_high - last_low
    if day_range > 0:
        close_position = (last_close - last_low) / day_range * 100
    else:
        close_position = 50.0

    is_bullish = last_close > last_open

    # 5日高値ブレイクアウト（8点）: 直近5日高値を更新したか
    high5 = high.rolling(5).max()
    prev_high5 = float(high5.iloc[-2]) if len(high5) >= 2 and pd.notna(high5.iloc[-2]) else float(_safe_last(high5))
    pct_from_5d_high = (last_close / float(_safe_last(high5)) - 1) * 100 if _safe_last(high5) > 0 else -100
    is_breakout_5d = last_close > prev_high5

    score = 0.0
    notes = []

    # 引け位置
    if close_position >= 80 and is_bullish:
        score += 7.0
        notes.append(f"高値引け({close_position:.0f}%)")
    elif close_position >= 60 and is_bullish:
        score += 4.0
        notes.append(f"上位引け({close_position:.0f}%)")
    elif is_bullish:
        score += 2.0
        notes.append(f"陽線({close_position:.0f}%)")
    else:
        notes.append(f"安値引け/陰線({close_position:.0f}%)")

    # 5日高値ブレイクアウト
    if is_breakout_5d:
        score += 8.0
        notes.append("5日高値ブレイク")
    elif pct_from_5d_high >= -1.5:
        score += 4.0
        notes.append(f"5日高値付近({pct_from_5d_high:.1f}%)")
    elif pct_from_5d_high >= -4.0:
        score += 1.0
        notes.append(f"5日高値下({pct_from_5d_high:.1f}%)")
    else:
        notes.append(f"5日高値乖離({pct_from_5d_high:.1f}%)")

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
