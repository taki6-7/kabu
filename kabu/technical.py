"""
テクニカル分析スコアリングモジュール（60点満点）

【設計思想：「上昇トレンド中の健全な押し目」を拾う】

過去の失敗分析:
  - 5日急騰銘柄を選ぶ → すでに動き切り → 翌日反落（平均回帰）
  - RSI(7)過熱ゾーン → 短期的買われすぎ → 翌日売り圧力

新方針:
  - 中長期トレンドが上向きの銘柄を土台として選ぶ
  - RSI(14)が健全ゾーン(50-68)にある = 過熱せず継続上昇中
  - 出来高が緩やかに増加 = 機関が少しずつ買い集めている
  - 20日高値ブレイク = 重要な抵抗線突破（追随買い発生しやすい）

シグナル内訳:
  - トレンド品質  15点: MA並び順 + MAの傾き（上向きか）
  - モメンタム健全性 15点: RSI(14)が50-68の健全ゾーン + MACD
  - 出来高蓄積    15点: 5日平均が20日平均を上回る持続的増加
  - ブレイクアウト  15点: 20日高値突破 + 引け足の強さ
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
    """トレンド品質スコア（15点）

    MA並び順（5 > 25 > 75）+ MA5が上向き の組み合わせで
    中長期的な上昇トレンドの強度を測定する。
    5日急騰ではなく「継続的な上昇トレンドにあるか」を評価。
    """
    if len(close) < 75:
        return 0.0, "データ不足"

    ma5  = close.rolling(5).mean()
    ma25 = close.rolling(25).mean()
    ma75 = close.rolling(75).mean()

    v5, v25, v75 = _safe_last(ma5), _safe_last(ma25), _safe_last(ma75)

    # MA5が5営業日前より上向きか（トレンドが継続加速しているか）
    ma5_prev = ma5.iloc[-6] if len(ma5) >= 6 and pd.notna(ma5.iloc[-6]) else v5
    ma5_rising = v5 > float(ma5_prev)

    # MA25も上向きか（中期トレンドが健在か）
    ma25_prev = ma25.iloc[-6] if len(ma25) >= 6 and pd.notna(ma25.iloc[-6]) else v25
    ma25_rising = v25 > float(ma25_prev)

    if v5 > v25 > v75:
        if ma5_rising and ma25_rising:
            return 15.0, f"完全上昇トレンド (5>{v5:.0f} 25>{v25:.0f} 75>{v75:.0f}, 両MA上向き)"
        elif ma5_rising:
            return 12.0, f"上昇トレンド+加速中 (5MA上向き)"
        else:
            return 9.0, f"パーフェクトオーダー (5MA横ばい)"
    elif v5 > v25:
        return 5.0 if ma5_rising else 3.0, f"短期上昇 ({'加速' if ma5_rising else '横ばい'})"
    elif v25 > v75:
        return 2.0, f"中期上昇のみ"
    else:
        return 0.0, f"下降トレンド"


def score_momentum(close: pd.Series) -> tuple[float, str]:
    """モメンタム健全性スコア（15点）

    RSI(14)が50-68の「健全ゾーン」にある銘柄を高評価。
    過熱（RSI>70）は翌日反落リスクが高いため減点。
    MACDでトレンド継続の確認を行う。
    """
    if len(close) < 30:
        return 0.0, "データ不足"

    rsi   = _calc_rsi(close, period=14)
    macd, signal = _calc_macd(close)

    rsi_val    = _safe_last(rsi)
    macd_val   = _safe_last(macd)
    signal_val = _safe_last(signal)
    prev_macd   = macd.iloc[-2] if len(macd) >= 2 else macd_val
    prev_signal = signal.iloc[-2] if len(signal) >= 2 else signal_val

    score = 0.0
    notes = []

    # RSI(14)スコア（8点）
    # 50-68: 上昇継続中かつ過熱していない「最良ゾーン」
    # >70:   短期過熱 → 反落しやすい → 低評価
    # <50:   上昇力不足 → 低評価
    if 55 <= rsi_val <= 68:
        score += 8.0
        notes.append(f"RSI健全ゾーン({rsi_val:.1f})")
    elif 50 <= rsi_val < 55:
        score += 5.0
        notes.append(f"RSI上昇圏({rsi_val:.1f})")
    elif 68 < rsi_val <= 75:
        score += 3.0
        notes.append(f"RSIやや過熱({rsi_val:.1f})")
    elif 45 <= rsi_val < 50:
        score += 2.0
        notes.append(f"RSI中立({rsi_val:.1f})")
    else:
        notes.append(f"RSI{'過熱' if rsi_val > 75 else '弱気'}({rsi_val:.1f})")

    # MACDゴールデンクロス（7点）
    golden_cross = (prev_macd < prev_signal) and (macd_val > signal_val)
    macd_above   = macd_val > signal_val

    if golden_cross:
        score += 7.0
        notes.append("MACDゴールデンクロス")
    elif macd_above:
        score += 3.0
        notes.append("MACD買いゾーン")
    else:
        notes.append("MACD売りゾーン")

    return score, " / ".join(notes)


def score_volume(volume: pd.Series) -> tuple[float, str]:
    """出来高蓄積スコア（15点）

    直近5日平均 vs 20日平均で「じわじわ増えている」を捉える。
    単日スパイクは機関買いではなく需給イベントの場合も多く除外。
    5日/20日比が高いほど機関が継続的に買い集めている可能性。
    """
    if len(volume) < 20:
        return 0.0, "データ不足"

    avg5  = volume.rolling(5).mean()
    avg20 = volume.rolling(20).mean()
    avg5_val  = _safe_last(avg5)
    avg20_val = _safe_last(avg20)

    if avg20_val == 0:
        return 0.0, "出来高データなし"

    ratio = avg5_val / avg20_val  # 5日平均 / 20日平均

    if ratio >= 1.6:
        return 15.0, f"出来高蓄積大({ratio:.2f}倍)"
    elif ratio >= 1.3:
        return 10.0, f"出来高蓄積中({ratio:.2f}倍)"
    elif ratio >= 1.05:
        return 5.0, f"出来高やや増加({ratio:.2f}倍)"
    elif ratio >= 0.85:
        return 2.0, f"出来高平均並み({ratio:.2f}倍)"
    else:
        return 0.0, f"出来高減少({ratio:.2f}倍)"


def score_price_action(high: pd.Series, low: pd.Series,
                       close: pd.Series, open_: pd.Series) -> tuple[float, str]:
    """ブレイクアウトスコア（15点）

    20日高値ブレイクアウト（重要な抵抗線突破）は翌日の追随買いを呼ぶ。
    引け足が高位置（上髭なし）であれば更にポジティブ。
    5日高値ブレイクより20日高値の方が市場参加者に意識される。
    """
    if len(close) < 25:
        return 0.0, "データ不足"

    last_close = _safe_last(close)
    last_high  = _safe_last(high)
    last_low   = _safe_last(low)
    last_open  = _safe_last(open_)

    # 20日高値ブレイクアウト（8点）
    high20 = high.rolling(20).max()
    prev_high20 = float(high20.iloc[-2]) if len(high20) >= 2 and pd.notna(high20.iloc[-2]) else float(_safe_last(high20))
    pct_from_20d = (last_close / float(_safe_last(high20)) - 1) * 100 if _safe_last(high20) > 0 else -100
    is_breakout_20d = last_close > prev_high20

    # 引け位置スコア（7点）: 日中値幅の何%の位置で引けたか
    day_range = last_high - last_low
    close_pos = (last_close - last_low) / day_range * 100 if day_range > 0 else 50.0
    is_bullish = last_close > last_open

    score = 0.0
    notes = []

    # 20日高値ブレイクアウト
    if is_breakout_20d:
        score += 8.0
        notes.append(f"20日高値ブレイク")
    elif pct_from_20d >= -2.0:
        score += 5.0
        notes.append(f"20日高値付近({pct_from_20d:.1f}%)")
    elif pct_from_20d >= -5.0:
        score += 2.0
        notes.append(f"20日高値下({pct_from_20d:.1f}%)")
    else:
        notes.append(f"20日高値乖離({pct_from_20d:.1f}%)")

    # 引け位置
    if close_pos >= 75 and is_bullish:
        score += 7.0
        notes.append(f"高値引け({close_pos:.0f}%)")
    elif close_pos >= 55 and is_bullish:
        score += 4.0
        notes.append(f"上位引け({close_pos:.0f}%)")
    elif is_bullish:
        score += 2.0
        notes.append(f"陽線({close_pos:.0f}%)")
    else:
        notes.append(f"安値引け/陰線({close_pos:.0f}%)")

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

        close  = df["Close"].squeeze()
        high   = df["High"].squeeze()
        low    = df["Low"].squeeze()
        open_  = df["Open"].squeeze()
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
