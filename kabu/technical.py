"""
テクニカル分析スコアリングモジュール（60点満点）

【設計思想：プロの実績ある手法を組み合わせる】

1. Minerviniトレンドテンプレート（score_trend）
   「Stage 2上昇トレンド」を5つの条件で判定。
   機関投資家が買いやすい銘柄構造を再現。

2. 12-1ヶ月モメンタム因子（score_momentum）
   Jegadeesh-Titman(1993)の学術的に実証されたファクター。
   「直近1ヶ月を除いた12ヶ月リターン」で短期反転ノイズを除去。

3. 出来高蓄積（score_volume）
   5日平均/20日平均比で機関の継続的な買い集めを捕捉。

4. ボリンジャーバンド + 引け足（score_price_action）
   バンドウォーク（上バンドを伝う上昇）でエントリータイミングを判定。

シグナル内訳:
  - トレンドテンプレート  15点: Minervini 5条件のうち何条件満たすか
  - 12-1ヶ月モメンタム   15点: 中期リターン + MACD確認
  - 出来高蓄積           15点: 5日平均 / 20日平均比
  - BBバンドウォーク      15点: アッパーバンド接触 + 高値引け
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
    """Minerviniトレンドテンプレート（15点）

    Mark MinerviniのSEPA手法をベースに「Stage 2上昇トレンド」を5条件で判定。
    プロの機関投資家が買いやすい銘柄構造を数値化する。

    条件（日本市場向けにMA期間を調整: 5/25/75日）:
      1. 終値 > 25MA > 75MA  （価格がMAスタック上位）
      2. 終値 > 5MA          （短期的にも強い）
      3. 75MAが上向き         （長期トレンドが継続中）
      4. 52週高値の75%以上    （過去1年の強さ: 25%以上の下落は除外）
      5. 52週安値の130%以上   （しっかりした底からの上昇確認）

    5条件達成: 15点 / 4条件: 11点 / 3条件: 6点 / 2条件: 2点 / それ以下: 0点
    """
    if len(close) < 75:
        return 0.0, "データ不足"

    ma5  = close.rolling(5).mean()
    ma25 = close.rolling(25).mean()
    ma75 = close.rolling(75).mean()

    v_close = float(close.iloc[-1])
    v5  = _safe_last(ma5)
    v25 = _safe_last(ma25)
    v75 = _safe_last(ma75)

    # 75MA上向き: 4週間前(約20営業日)と比較
    ma75_prev = ma75.iloc[-21] if len(ma75) >= 21 and pd.notna(ma75.iloc[-21]) else v75
    ma75_rising = v75 > float(ma75_prev)

    # 52週高値・安値（データが足りない場合は利用可能な分で計算）
    window = min(252, len(close))
    high52 = float(close.rolling(window).max().iloc[-1])
    low52  = float(close.rolling(window).min().iloc[-1])
    pct_from_high = v_close / high52 if high52 > 0 else 0
    pct_from_low  = v_close / low52  if low52 > 0 else 0

    # 5条件チェック
    conds = [
        v_close > v25 and v25 > v75,   # 1. MAスタック
        v_close > v5,                   # 2. 短期MAより上
        ma75_rising,                    # 3. 長期MAが上向き
        pct_from_high >= 0.75,          # 4. 52週高値の75%以上
        pct_from_low  >= 1.30,          # 5. 52週安値の130%以上
    ]
    n = sum(conds)
    labels = ["MAスタック", "5MA上", "75MA上向", "高値圏", "安値比+30%"]
    met = [labels[i] for i, c in enumerate(conds) if c]

    scores_map = {5: 15.0, 4: 11.0, 3: 6.0, 2: 2.0}
    score = scores_map.get(n, 0.0)
    note  = f"Stage2条件{n}/5 [{', '.join(met)}]" if met else f"条件未達({n}/5)"
    return score, note


def score_momentum(close: pd.Series) -> tuple[float, str]:
    """12-1ヶ月モメンタム因子 + MACD（15点）

    Jegadeesh & Titman (1993) の学術的に実証されたモメンタム因子。
    「直近12ヶ月のリターンから直近1ヶ月を除いたもの」を使う。
    直近1ヶ月を除く理由: 短期的な平均回帰（反転）ノイズを取り除くため。

    例: 今日が基準なら「約11ヶ月前〜約1ヶ月前」の価格変動を評価。
    この期間に大きく上昇した銘柄は翌月も上昇しやすいことが統計的に示されている。

    スコア（8点）:
      +15%以上: 8点 / +8%以上: 6点 / +3%以上: 4点 / +0%以上: 2点 / マイナス: 0点

    MACD（7点）: エントリータイミングの最終確認
    """
    if len(close) < 30:
        return 0.0, "データ不足"

    score = 0.0
    notes = []

    # ── 12-1ヶ月モメンタム（8点） ──
    # 約252営業日前〜約22営業日前のリターン
    end_idx   = -22   # 1ヶ月前（直近を除く）
    start_idx = -252  # 12ヶ月前

    if len(close) >= 252:
        price_start = float(close.iloc[start_idx])
        price_end   = float(close.iloc[end_idx])
    elif len(close) >= 66:
        # データ不足の場合は3ヶ月前〜1ヶ月前で代用（短期版）
        price_start = float(close.iloc[-66])
        price_end   = float(close.iloc[end_idx])
    else:
        price_start = price_end = float(close.iloc[0])

    momentum_pct = (price_end / price_start - 1) * 100 if price_start > 0 else 0.0
    period_label = "12-1月" if len(close) >= 252 else "3-1月"

    if momentum_pct >= 15.0:
        score += 8.0
        notes.append(f"モメンタム強({period_label}:{momentum_pct:+.1f}%)")
    elif momentum_pct >= 8.0:
        score += 6.0
        notes.append(f"モメンタム中({period_label}:{momentum_pct:+.1f}%)")
    elif momentum_pct >= 3.0:
        score += 4.0
        notes.append(f"モメンタム弱({period_label}:{momentum_pct:+.1f}%)")
    elif momentum_pct >= 0.0:
        score += 2.0
        notes.append(f"モメンタム微({period_label}:{momentum_pct:+.1f}%)")
    else:
        notes.append(f"モメンタム負({period_label}:{momentum_pct:+.1f}%)")

    # ── MACD（7点）: 短期エントリータイミング確認 ──
    macd, signal = _calc_macd(close)
    macd_val    = _safe_last(macd)
    signal_val  = _safe_last(signal)
    prev_macd   = macd.iloc[-2] if len(macd) >= 2 else macd_val
    prev_signal = signal.iloc[-2] if len(signal) >= 2 else signal_val

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


def _calc_bollinger(close: pd.Series, period: int = 20, std_dev: float = 2.0):
    """ボリンジャーバンド計算（中央線・上限・下限）"""
    mid   = close.rolling(period).mean()
    sigma = close.rolling(period).std(ddof=0)
    upper = mid + std_dev * sigma
    lower = mid - std_dev * sigma
    return mid, upper, lower


def score_price_action(high: pd.Series, low: pd.Series,
                       close: pd.Series, open_: pd.Series) -> tuple[float, str]:
    """ボリンジャーバンド + 引け足スコア（15点）

    【バンドウォーク検出（8点）】
    上のバンドを伝って上昇し続ける「バンドウォーク」は
    強いトレンドの証拠。以下の段階で評価する:
      - 3日連続でアッパーバンド以上 = バンドウォーク確定(8点)
      - 当日アッパーバンド以上      = バンドタッチ(6点)
      - アッパーバンドの2%以内      = バンド接近(4点)
      - 中心線より上               = ミドル上位(2点)
      - 中心線以下                 = 対象外(0点)

    【引け足強度（7点）】
    日中値幅の何%の位置で引けたか。
    高値引け（上髭なし）は翌日続伸サイン。
    """
    if len(close) < 25:
        return 0.0, "データ不足"

    last_close = _safe_last(close)
    last_high  = _safe_last(high)
    last_low   = _safe_last(low)
    last_open  = _safe_last(open_)

    # ── ボリンジャーバンド計算 ──
    mid, upper, lower = _calc_bollinger(close)
    upper_val = _safe_last(upper)
    mid_val   = _safe_last(mid)

    # バンドウォーク判定（直近3日連続でアッパーバンド以上かチェック）
    if len(close) >= 3 and upper_val > 0:
        band_walk_days = 0
        for j in range(1, 4):  # 直近3日分
            if len(close) >= j and len(upper) >= j:
                c = close.iloc[-j]
                u = upper.iloc[-j]
                if pd.notna(c) and pd.notna(u) and float(c) >= float(u):
                    band_walk_days += 1
                else:
                    break  # 連続が途切れたら終了
    else:
        band_walk_days = 0

    pct_from_upper = (last_close / upper_val - 1) * 100 if upper_val > 0 else -100

    # ── 引け位置スコア ──
    day_range = last_high - last_low
    close_pos = (last_close - last_low) / day_range * 100 if day_range > 0 else 50.0
    is_bullish = last_close > last_open

    score = 0.0
    notes = []

    # バンドウォークスコア（8点）
    if band_walk_days >= 3:
        score += 8.0
        notes.append(f"バンドウォーク{band_walk_days}日継続")
    elif band_walk_days >= 1 or pct_from_upper >= 0:
        score += 6.0
        notes.append(f"アッパーバンドタッチ({pct_from_upper:+.1f}%)")
    elif pct_from_upper >= -2.0:
        score += 4.0
        notes.append(f"バンド接近({pct_from_upper:.1f}%)")
    elif last_close > mid_val and mid_val > 0:
        score += 2.0
        notes.append(f"ミドル上位({pct_from_upper:.1f}%)")
    else:
        notes.append(f"ミドル以下({pct_from_upper:.1f}%)")

    # 引け位置スコア（7点）
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
