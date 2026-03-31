#!/usr/bin/env python3
"""
バックテストエンジン - 過去2年間のスクリーニングロジック検証

使い方:
    python backtest.py
    python backtest.py --years 2 --top-n 5 --rebalance weekly
    python backtest.py --years 1 --top-n 3 --rebalance monthly

出力:
    reports/backtest_YYYYMMDD_HHMM.html
"""

import argparse
import json
import logging
import os
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

from kabu.screener import PRIME_TICKERS_SAMPLE
from kabu.technical import score_trend, score_momentum, score_volume, score_price_action

# ─────────────────────────────────────────────
# 設定
# ─────────────────────────────────────────────
BENCHMARK_TICKER = "^N225"
INITIAL_CAPITAL = 1_000_000   # 初期資金 ¥100万
COMMISSION_RATE = 0.001        # 片道0.1%手数料（日次取引は信用取引等で低減可能）

# 日次戦略用 ストップロス/テイクプロフィット
TAKE_PROFIT_PCT = 0.03   # +3% でテイクプロフィット
STOP_LOSS_PCT   = 0.02   # -2% でストップロス

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# データ取得
# ─────────────────────────────────────────────

def download_all_data(tickers: list, start: str, end: str) -> dict:
    """全銘柄の履歴データを一括ダウンロード"""
    logger.info(f"{len(tickers)} 銘柄のデータをダウンロード中... ({start} ~ {end})")
    all_data = {}
    batch_size = 10

    for i in range(0, len(tickers), batch_size):
        batch = tickers[i: i + batch_size]
        logger.info(f"  バッチ {i // batch_size + 1}/{(len(tickers) - 1) // batch_size + 1}: {batch}")

        try:
            raw = yf.download(
                batch,
                start=start,
                end=end,
                auto_adjust=True,
                progress=False,
                group_by="ticker",
            )

            for ticker in batch:
                try:
                    df = raw if len(batch) == 1 else raw[ticker]
                    df = df.dropna(how="all")
                    if len(df) >= 80:
                        all_data[ticker] = df
                except Exception as e:
                    logger.debug(f"{ticker} スキップ: {e}")

        except Exception as e:
            logger.warning(f"バッチエラー: {e}")

        time.sleep(0.5)

    logger.info(f"データ取得完了: {len(all_data)}/{len(tickers)} 銘柄")
    return all_data


# ─────────────────────────────────────────────
# スコア計算（ウォークフォワード）
# ─────────────────────────────────────────────

def calc_score_from_slice(df_slice: pd.DataFrame) -> float:
    """データスライスからテクニカルスコアを計算（60点満点）"""
    if len(df_slice) < 80:
        return 0.0
    try:
        close  = df_slice["Close"].squeeze()
        high   = df_slice["High"].squeeze()
        low    = df_slice["Low"].squeeze()
        open_  = df_slice["Open"].squeeze()
        volume = df_slice["Volume"].squeeze()

        t_score, _ = score_trend(close)
        m_score, _ = score_momentum(close)
        v_score, _ = score_volume(volume)
        p_score, _ = score_price_action(high, low, close, open_)
        return t_score + m_score + v_score + p_score
    except Exception:
        return 0.0


# ─────────────────────────────────────────────
# リバランス日生成
# ─────────────────────────────────────────────

def get_rebalance_dates(start: pd.Timestamp, end: pd.Timestamp, frequency: str) -> list:
    """リバランス日付リストを生成（営業日ベース）"""
    bdays = pd.bdate_range(start=start, end=end, freq="B")

    if frequency == "daily":
        return list(bdays)

    elif frequency == "weekly":
        dates, last_key = [], None
        for d in bdays:
            key = (d.year, d.isocalendar()[1])
            if key != last_key:
                dates.append(d)
                last_key = key
        return dates

    elif frequency == "monthly":
        seen, dates = set(), []
        for d in bdays:
            key = (d.year, d.month)
            if key not in seen:
                seen.add(key)
                dates.append(d)
        return dates

    else:  # biweekly
        dates = []
        for d in bdays:
            if not dates or (d - dates[-1]).days >= 14:
                dates.append(d)
        return dates


# ─────────────────────────────────────────────
# バックテスト本体
# ─────────────────────────────────────────────

def _simulate_exit(df: pd.DataFrame, entry_date: pd.Timestamp,
                   entry_price: float, use_sl_tp: bool) -> tuple:
    """翌営業日のOHLCデータでSL/TP発動をシミュレート。
    日次OHLCV使用のため安値がSL以下なら-2%、高値がTP以上なら+3%で約定と仮定。
    Returns: (exit_price, exit_reason)
    """
    future = df[df.index > entry_date]
    if len(future) == 0:
        return entry_price, "データなし"

    next_row = future.iloc[0]
    next_high  = float(next_row["High"])
    next_low   = float(next_row["Low"])
    next_close = float(next_row["Close"])

    if use_sl_tp:
        tp_price = entry_price * (1 + TAKE_PROFIT_PCT)
        sl_price = entry_price * (1 - STOP_LOSS_PCT)

        # 寄り付きギャップダウンでSL以下になった場合は翌日始値で決済
        next_open = float(next_row["Open"]) if "Open" in next_row else next_close
        if next_open <= sl_price:
            return next_open, f"ギャップダウンSL"

        # 安値がSL以下 → SL発動
        if next_low <= sl_price:
            return sl_price, f"SL(-{STOP_LOSS_PCT*100:.0f}%)"
        # 高値がTP以上 → TP発動
        if next_high >= tp_price:
            return tp_price, f"TP(+{TAKE_PROFIT_PCT*100:.0f}%)"

    # 翌日引けで決済
    return next_close, "引け決済"


def run_backtest(all_data: dict, rebalance_dates: list, top_n: int,
                 use_sl_tp: bool = False) -> dict:
    """ウォークフォワード・バックテスト実行"""
    capital = float(INITIAL_CAPITAL)
    current_holdings = []   # list of (ticker, entry_price, shares, entry_date)
    trades = []
    portfolio_values = []

    for i, date in enumerate(rebalance_dates[:-1]):
        # ── スコア計算（date以前のデータのみ使用） ──
        scores = {}
        for ticker, df in all_data.items():
            df_slice = df[df.index <= date]
            score = calc_score_from_slice(df_slice)
            if score > 0:
                scores[ticker] = score

        if not scores:
            portfolio_values.append({"date": date.isoformat(), "value": capital,
                                     "top_tickers": [], "top_scores": {}})
            continue

        top_tickers = sorted(scores, key=lambda x: scores[x], reverse=True)[:top_n]

        # ── 前回ポジション清算 ──
        exit_value = 0.0
        for ticker, entry_price, shares, entry_date in current_holdings:
            df = all_data.get(ticker)
            if df is not None:
                exit_price, exit_reason = _simulate_exit(df, entry_date, entry_price, use_sl_tp)
            else:
                exit_price, exit_reason = entry_price, "データなし"

            gross = shares * exit_price
            net = gross - gross * COMMISSION_RATE
            exit_value += net

            pnl_pct = (exit_price / entry_price - 1) * 100 if entry_price > 0 else 0
            trades.append({
                "date": date.isoformat(),
                "ticker": ticker,
                "entry_price": round(entry_price, 2),
                "exit_price": round(exit_price, 2),
                "exit_reason": exit_reason,
                "pnl_pct": round(pnl_pct, 2),
                "pnl": round(net - shares * entry_price, 0),
            })

        if current_holdings:
            capital = exit_value

        # ── 新規ポジション構築 ──
        per_stock = capital / top_n
        new_holdings = []
        for ticker in top_tickers:
            df = all_data.get(ticker)
            if df is None:
                continue
            future = df[df.index >= date]
            if len(future) == 0:
                continue
            entry_price = float(future["Close"].iloc[0])
            invest = per_stock - per_stock * COMMISSION_RATE
            shares = invest / entry_price if entry_price > 0 else 0
            new_holdings.append((ticker, entry_price, shares, date))

        current_holdings = new_holdings

        portfolio_values.append({
            "date": date.isoformat(),
            "value": round(capital, 0),
            "top_tickers": top_tickers,
            "top_scores": {t: round(scores[t], 1) for t in top_tickers},
        })

        logger.info(
            f"{date.date()} | 資産: ¥{capital:>10,.0f} | "
            f"TOP{top_n}: {', '.join(top_tickers[:3])}"
        )

    # ── 最終清算 ──
    final_value = 0.0
    for ticker, entry_price, shares, entry_date in current_holdings:
        df = all_data.get(ticker)
        last_price = float(df["Close"].iloc[-1]) if df is not None else entry_price
        gross = shares * last_price
        final_value += gross - gross * COMMISSION_RATE

    if current_holdings:
        capital = final_value

    return {
        "trades": trades,
        "portfolio_values": portfolio_values,
        "final_capital": round(capital, 0),
    }


# ─────────────────────────────────────────────
# パフォーマンス指標
# ─────────────────────────────────────────────

def calc_metrics(result: dict, benchmark_data: pd.DataFrame) -> dict:
    pv = result["portfolio_values"]
    trades = result["trades"]
    final = result["final_capital"]

    if not pv:
        return {}

    start_ts = pd.Timestamp(pv[0]["date"])
    end_ts   = pd.Timestamp(pv[-1]["date"])
    years = max((end_ts - start_ts).days / 365.25, 0.01)

    total_return = (final / INITIAL_CAPITAL - 1) * 100
    annual_return = ((final / INITIAL_CAPITAL) ** (1 / years) - 1) * 100

    # 最大ドローダウン
    values = [v["value"] for v in pv] + [final]
    peak, max_dd = values[0], 0.0
    for v in values:
        peak = max(peak, v)
        dd = (v - peak) / peak * 100
        max_dd = min(max_dd, dd)

    # 勝率
    winning = [t for t in trades if t["pnl"] > 0]
    losing  = [t for t in trades if t["pnl"] <= 0]
    win_rate = len(winning) / len(trades) * 100 if trades else 0
    avg_win  = float(np.mean([t["pnl_pct"] for t in winning])) if winning else 0
    avg_loss = float(np.mean([t["pnl_pct"] for t in losing]))  if losing  else 0
    profit_factor = (
        abs(sum(t["pnl"] for t in winning)) / abs(sum(t["pnl"] for t in losing))
        if losing and sum(t["pnl"] for t in losing) != 0 else float("inf")
    )

    # 日次シャープレシオ（年率換算）
    daily_rets = []
    for i in range(1, len(pv)):
        p, c = pv[i - 1]["value"], pv[i]["value"]
        if p > 0:
            daily_rets.append((c - p) / p)
    if daily_rets:
        mu, sigma = np.mean(daily_rets), np.std(daily_rets)
        sharpe = float(mu / sigma * np.sqrt(250)) if sigma > 0 else 0.0
    else:
        sharpe = 0.0

    # TP/SL発動内訳
    tp_count = len([t for t in trades if "TP" in t.get("exit_reason", "")])
    sl_count = len([t for t in trades if "SL" in t.get("exit_reason", "")])

    # ベンチマーク(N225)リターン
    bm_start = benchmark_data[benchmark_data.index >= start_ts]
    bm_end   = benchmark_data[benchmark_data.index <= end_ts]
    if len(bm_start) > 0 and len(bm_end) > 0:
        bm_return = (float(bm_end["Close"].iloc[-1]) / float(bm_start["Close"].iloc[0]) - 1) * 100
    else:
        bm_return = 0.0

    return {
        "total_return_pct":    round(total_return, 2),
        "annual_return_pct":   round(annual_return, 2),
        "max_drawdown_pct":    round(max_dd, 2),
        "sharpe_ratio":        round(sharpe, 2),
        "win_rate_pct":        round(win_rate, 1),
        "avg_win_pct":         round(avg_win, 2),
        "avg_loss_pct":        round(avg_loss, 2),
        "profit_factor":       round(profit_factor, 2) if profit_factor != float("inf") else "∞",
        "total_trades":        len(trades),
        "tp_count":            tp_count,
        "sl_count":            sl_count,
        "benchmark_return_pct": round(bm_return, 2),
        "alpha_pct":           round(total_return - bm_return, 2),
        "initial_capital":     INITIAL_CAPITAL,
        "final_capital":       int(final),
        "backtest_years":      round(years, 1),
    }


# ─────────────────────────────────────────────
# HTMLレポート生成
# ─────────────────────────────────────────────

def generate_html_report(metrics: dict, result: dict, benchmark_data: pd.DataFrame,
                          top_n: int, frequency: str) -> str:
    pv = result["portfolio_values"]
    trades = result["trades"]
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # チャートデータ
    chart_labels = json.dumps([v["date"][:10] for v in pv])
    chart_portfolio = json.dumps([v["value"] for v in pv])

    # N225チャートデータ（ポートフォリオと同スケール）
    bm_values = []
    if pv:
        start_ts = pd.Timestamp(pv[0]["date"])
        bm_slice = benchmark_data[benchmark_data.index >= start_ts]
        if len(bm_slice) > 0:
            bm_base = float(bm_slice["Close"].iloc[0])
            for v in pv:
                d = pd.Timestamp(v["date"])
                nearest = bm_slice[bm_slice.index <= d]
                if len(nearest) > 0:
                    bm_price = float(nearest["Close"].iloc[-1])
                    bm_values.append(round(INITIAL_CAPITAL * bm_price / bm_base, 0))
                else:
                    bm_values.append(INITIAL_CAPITAL)
    chart_benchmark = json.dumps(bm_values)

    # 最近30トレード（直近順）
    recent_trades = sorted(trades, key=lambda x: x["date"], reverse=True)[:30]
    trade_rows = ""
    for t in recent_trades:
        color = "#4ec9b0" if t["pnl"] > 0 else "#f48771"
        sign  = "+" if t["pnl"] > 0 else ""
        reason = t.get("exit_reason", "")
        trade_rows += f"""
        <tr>
            <td>{t['date'][:10]}</td>
            <td>{t['ticker']}</td>
            <td>¥{t['entry_price']:,.0f}</td>
            <td>¥{t['exit_price']:,.0f}</td>
            <td style="color:{color};">{sign}{t['pnl_pct']:.2f}%</td>
            <td style="color:{color};">{sign}¥{t['pnl']:,.0f}</td>
            <td style="font-size:11px;color:#6c7086;">{reason}</td>
        </tr>"""

    # 指標カード
    def metric_card(label, value, sub="", positive_good=True):
        if isinstance(value, float) and value < 0 and positive_good:
            vcolor = "#f48771"
        elif isinstance(value, (int, float)) and value > 0 and positive_good:
            vcolor = "#4ec9b0"
        else:
            vcolor = "#d4d4d4"
        return f"""
        <div class="card">
            <div class="card-label">{label}</div>
            <div class="card-value" style="color:{vcolor};">{value}</div>
            <div class="card-sub">{sub}</div>
        </div>"""

    tr  = metrics.get("total_return_pct", 0)
    ar  = metrics.get("annual_return_pct", 0)
    bm  = metrics.get("benchmark_return_pct", 0)
    al  = metrics.get("alpha_pct", 0)
    dd  = metrics.get("max_drawdown_pct", 0)
    sh  = metrics.get("sharpe_ratio", 0)
    wr  = metrics.get("win_rate_pct", 0)
    pf  = metrics.get("profit_factor", 0)
    fc  = metrics.get("final_capital", 0)
    yrs = metrics.get("backtest_years", 0)
    tp_c = metrics.get("tp_count", 0)
    sl_c = metrics.get("sl_count", 0)
    total_t = metrics.get("total_trades", 0)

    cards = (
        metric_card("累計リターン", f"{'+' if tr >= 0 else ''}{tr:.2f}%", f"初期 ¥{INITIAL_CAPITAL:,} → 最終 ¥{fc:,}")
        + metric_card("年率リターン", f"{'+' if ar >= 0 else ''}{ar:.2f}%", f"期間 {yrs:.1f}年")
        + metric_card("最大ドローダウン", f"{dd:.2f}%", "低いほど良い", positive_good=False)
        + metric_card("シャープレシオ", f"{sh:.2f}", "1.0以上が目安")
        + metric_card("勝率", f"{wr:.1f}%", f"総: {total_t}回 / TP: {tp_c}回 / SL: {sl_c}回")
        + metric_card("プロフィットファクター", str(pf), "1.0超で利益超過")
        + metric_card("N225リターン", f"{'+' if bm >= 0 else ''}{bm:.2f}%", "ベンチマーク")
        + metric_card("超過リターン(α)", f"{'+' if al >= 0 else ''}{al:.2f}%", "戦略 − N225")
    )

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<title>バックテスト結果 | kabu</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #1e1e2e; color: #cdd6f4; font-family: 'Segoe UI', sans-serif; font-size: 14px; }}
  .header {{ background: linear-gradient(135deg, #313244, #1e1e2e); padding: 24px 32px; border-bottom: 2px solid #45475a; }}
  .header h1 {{ font-size: 24px; color: #89b4fa; }}
  .header p  {{ color: #6c7086; margin-top: 4px; }}
  .section {{ padding: 24px 32px; }}
  .section h2 {{ font-size: 16px; color: #89b4fa; margin-bottom: 16px; border-left: 3px solid #89b4fa; padding-left: 8px; }}
  .cards {{ display: flex; flex-wrap: wrap; gap: 12px; }}
  .card {{ background: #313244; border-radius: 8px; padding: 16px 20px; min-width: 160px; flex: 1; }}
  .card-label {{ font-size: 11px; color: #6c7086; text-transform: uppercase; letter-spacing: 0.5px; }}
  .card-value {{ font-size: 24px; font-weight: bold; margin: 6px 0 2px; }}
  .card-sub {{ font-size: 11px; color: #6c7086; }}
  .chart-wrap {{ background: #313244; border-radius: 8px; padding: 20px; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th {{ background: #45475a; color: #89b4fa; padding: 8px 12px; text-align: left; font-size: 12px; }}
  td {{ padding: 7px 12px; border-bottom: 1px solid #313244; font-size: 13px; }}
  tr:hover td {{ background: #313244; }}
  .note {{ background: #313244; border-radius: 8px; padding: 12px 16px; font-size: 12px; color: #6c7086; line-height: 1.6; }}
</style>
</head>
<body>

<div class="header">
  <h1>📊 バックテスト結果</h1>
  <p>生成日時: {now} ／ 対象期間: {yrs:.1f}年 ／ リバランス: {frequency} ／ 上位{top_n}銘柄保有</p>
</div>

<div class="section">
  <h2>パフォーマンス指標</h2>
  <div class="cards">{cards}</div>
</div>

<div class="section">
  <h2>ポートフォリオ推移</h2>
  <div class="chart-wrap">
    <canvas id="perfChart" height="80"></canvas>
  </div>
</div>

<div class="section">
  <h2>直近トレード履歴</h2>
  <table>
    <tr><th>日付</th><th>銘柄</th><th>エントリー</th><th>エグジット</th><th>損益(%)</th><th>損益(¥)</th><th>決済理由</th></tr>
    {trade_rows}
  </table>
</div>

<div class="section">
  <div class="note">
    ⚠️ 注意事項: このバックテストはテクニカルスコア（60点満点）のみを使用しています。
    ファンダメンタル指標（PER/PBR/業績）は過去データがyfinanceで取得できないためスキップしています。
    サバイバーシップバイアス（現在生き残っている銘柄のみ対象）があります。
    手数料は片道0.1%を考慮済みです。過去の結果は将来の成績を保証しません。
  </div>
</div>

<script>
const ctx = document.getElementById('perfChart').getContext('2d');
new Chart(ctx, {{
  type: 'line',
  data: {{
    labels: {chart_labels},
    datasets: [
      {{
        label: 'ポートフォリオ',
        data: {chart_portfolio},
        borderColor: '#89b4fa',
        backgroundColor: 'rgba(137,180,250,0.08)',
        borderWidth: 2,
        pointRadius: 0,
        fill: true,
        tension: 0.3,
      }},
      {{
        label: '日経225（同スケール）',
        data: {chart_benchmark},
        borderColor: '#f38ba8',
        backgroundColor: 'rgba(243,139,168,0.05)',
        borderWidth: 1.5,
        pointRadius: 0,
        fill: false,
        tension: 0.3,
      }},
    ]
  }},
  options: {{
    responsive: true,
    interaction: {{ mode: 'index', intersect: false }},
    plugins: {{
      legend: {{ labels: {{ color: '#cdd6f4' }} }},
      tooltip: {{
        callbacks: {{
          label: ctx => ' ¥' + ctx.parsed.y.toLocaleString()
        }}
      }}
    }},
    scales: {{
      x: {{ ticks: {{ color: '#6c7086', maxTicksLimit: 12 }}, grid: {{ color: '#313244' }} }},
      y: {{
        ticks: {{ color: '#6c7086', callback: v => '¥' + v.toLocaleString() }},
        grid: {{ color: '#313244' }}
      }}
    }}
  }}
}});
</script>
</body>
</html>"""

    return html


# ─────────────────────────────────────────────
# エントリーポイント
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="kabu バックテストエンジン")
    parser.add_argument("--years",     type=float, default=2.0,        help="バックテスト期間（年）")
    parser.add_argument("--top-n",     type=int,   default=5,          help="保有銘柄数")
    parser.add_argument("--rebalance", type=str,   default="daily",
                        choices=["daily", "weekly", "biweekly", "monthly"], help="リバランス頻度")
    parser.add_argument("--sl-tp",     action="store_true",             help="ストップロス/テイクプロフィットを有効化")
    args = parser.parse_args()

    end_date   = datetime.today()
    # ウォームアップ込みで+1年分余分に取得
    start_date = end_date - timedelta(days=int((args.years + 1) * 365))

    start_str = start_date.strftime("%Y-%m-%d")
    end_str   = end_date.strftime("%Y-%m-%d")

    # ── 1. データダウンロード ──
    all_data = download_all_data(PRIME_TICKERS_SAMPLE, start=start_str, end=end_str)

    logger.info(f"ベンチマーク ({BENCHMARK_TICKER}) ダウンロード中...")
    benchmark_data = yf.download(BENCHMARK_TICKER, start=start_str, end=end_str,
                                 auto_adjust=True, progress=False)

    # ── 2. リバランス日生成（バックテスト本番期間のみ） ──
    bt_start = pd.Timestamp(end_date - timedelta(days=int(args.years * 365)))
    bt_end   = pd.Timestamp(end_date)
    rebalance_dates = get_rebalance_dates(bt_start, bt_end, args.rebalance)
    logger.info(f"リバランス日数: {len(rebalance_dates)} 回")

    # ── 3. バックテスト実行 ──
    use_sl_tp = args.sl_tp
    logger.info(f"バックテスト実行中... SL/TP={'有効' if use_sl_tp else '無効'} (SL:-{STOP_LOSS_PCT*100:.0f}% / TP:+{TAKE_PROFIT_PCT*100:.0f}%)")
    result = run_backtest(all_data, rebalance_dates, top_n=args.top_n, use_sl_tp=use_sl_tp)

    # ── 4. 指標計算 ──
    metrics = calc_metrics(result, benchmark_data)

    # ── 5. 結果サマリー出力 ──
    print("\n" + "=" * 50)
    print("  バックテスト結果サマリー")
    print("=" * 50)
    print(f"  期間         : {metrics.get('backtest_years', 0):.1f}年")
    print(f"  累計リターン : {metrics.get('total_return_pct', 0):+.2f}%")
    print(f"  年率リターン : {metrics.get('annual_return_pct', 0):+.2f}%")
    print(f"  N225リターン : {metrics.get('benchmark_return_pct', 0):+.2f}%")
    print(f"  超過リターン : {metrics.get('alpha_pct', 0):+.2f}%")
    print(f"  最大DD       : {metrics.get('max_drawdown_pct', 0):.2f}%")
    print(f"  シャープ比   : {metrics.get('sharpe_ratio', 0):.2f}")
    print(f"  勝率         : {metrics.get('win_rate_pct', 0):.1f}%  ({metrics.get('total_trades', 0)}トレード)")
    print(f"  TP発動       : {metrics.get('tp_count', 0)}回")
    print(f"  SL発動       : {metrics.get('sl_count', 0)}回")
    print(f"  PF           : {metrics.get('profit_factor', 0)}")
    print(f"  最終資産     : ¥{metrics.get('final_capital', 0):,}")
    print("=" * 50 + "\n")

    # ── 6. HTMLレポート出力 ──
    os.makedirs("reports", exist_ok=True)
    fname = f"reports/backtest_{datetime.now().strftime('%Y%m%d_%H%M')}.html"
    html = generate_html_report(metrics, result, benchmark_data,
                                 top_n=args.top_n, frequency=args.rebalance)
    with open(fname, "w", encoding="utf-8") as f:
        f.write(html)

    logger.info(f"レポート出力: {fname}")
    print(f"レポート: {fname}")


if __name__ == "__main__":
    main()
