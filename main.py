"""
株式投資支援ツール — メインスクリプト

使い方:
  python main.py                  # 通常実行
  python main.py --dry-run        # メール送信なしで動作確認
  python main.py --top-n 3        # 推奨銘柄数を変更
"""

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

LOG_DIR = BASE_DIR / "logs"
REPORT_DIR = BASE_DIR / "reports"
LOG_DIR.mkdir(exist_ok=True)
REPORT_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            LOG_DIR / f"run_{datetime.now().strftime('%Y%m%d')}.log",
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger(__name__)

try:
    from kabu.screener import get_prime_tickers, apply_liquidity_filter
    from kabu.technical import calc_technical_score
    from kabu.fundamental import calc_fundamental_score
    from kabu.reporter import generate_html_report
    from kabu.notifier import send_report_email
except Exception as _import_err:
    logger.exception(f"モジュールのインポートに失敗しました: {_import_err}")
    sys.exit(1)


def run(top_n: int = 5, dry_run: bool = False):
    logger.info("=" * 60)
    logger.info("株式投資支援ツール 起動")
    logger.info("=" * 60)

    # Step 1: 銘柄リスト取得
    logger.info("Step 1: 東証プライム銘柄リスト取得")
    tickers = get_prime_tickers()
    total_screened = len(tickers)
    logger.info(f"対象銘柄数: {total_screened}")

    # Step 2: 流動性フィルター
    logger.info("Step 2: 流動性フィルター適用")
    min_volume = int(os.getenv("MIN_VOLUME", 500_000))
    min_market_cap = float(os.getenv("MIN_MARKET_CAP", 50_000_000_000))
    filtered = apply_liquidity_filter(tickers, min_volume=min_volume, min_market_cap=min_market_cap)
    liquidity_passed = len(filtered)

    # Step 3: テクニカルスコアリング（60点）
    logger.info("Step 3: テクニカルスコアリング")
    tech_results = []
    for i, ticker in enumerate(filtered, 1):
        logger.info(f"  [{i}/{liquidity_passed}] {ticker} テクニカル分析中...")
        result = calc_technical_score(ticker)
        if result["error"] is None:
            tech_results.append(result)

    # Step 4: ファンダスコアリング（40点）
    logger.info("Step 4: ファンダメンタル・ニューススコアリング")
    scored = []
    for i, tech in enumerate(tech_results, 1):
        ticker = tech["ticker"]
        logger.info(f"  [{i}/{len(tech_results)}] {ticker} ファンダ分析中...")
        fund = calc_fundamental_score(ticker)

        total_score = tech["total"] + fund["total"]

        scored.append({
            "ticker": ticker,
            "name": fund.get("name", ticker),
            "sector": fund.get("sector", "不明"),
            "total_score": round(total_score, 2),
            # テクニカル内訳
            "trend": tech["trend"],
            "momentum": tech["momentum"],
            "volume": tech["volume"],
            "price_action": tech["price_action"],
            # ファンダ内訳
            "us_market": fund["us_market"],
            "valuation": fund["valuation"],
            "earnings": fund["earnings"],
            # 詳細
            "technical_details": tech["details"],
            "fundamental_details": fund["details"],
        })

    # Step 5: ランキング・上位N銘柄選出
    scored.sort(key=lambda x: x["total_score"], reverse=True)
    recommendations = scored[:top_n]

    logger.info(f"\n{'='*60}")
    logger.info(f"推奨銘柄 TOP{top_n}")
    logger.info(f"{'='*60}")
    for rank, rec in enumerate(recommendations, 1):
        logger.info(
            f"#{rank} {rec['name']}({rec['ticker']}) "
            f"スコア: {rec['total_score']:.1f}/100"
        )
    logger.info("=" * 60)

    # Step 6: HTMLレポート生成
    logger.info("Step 5: HTMLレポート生成")
    report_path = generate_html_report(
        recommendations=recommendations,
        total_screened=total_screened,
        liquidity_passed=liquidity_passed,
        output_dir=str(REPORT_DIR),
    )
    logger.info(f"レポート保存: {report_path}")

    # Step 7: Gmail送信
    if dry_run:
        logger.info("Step 6: [DRY-RUN] メール送信スキップ")
    else:
        logger.info("Step 6: Gmail送信")
        gmail_address = os.getenv("GMAIL_ADDRESS")
        app_password = os.getenv("GMAIL_APP_PASSWORD")
        notify_to = os.getenv("NOTIFY_TO")

        if not all([gmail_address, app_password, notify_to]):
            logger.warning(".envにGmail設定がありません。メール送信をスキップします。")
            logger.warning("  GMAIL_ADDRESS, GMAIL_APP_PASSWORD, NOTIFY_TO を設定してください。")
        else:
            date_str = datetime.now().strftime("%Y年%m月%d日")
            success = send_report_email(
                gmail_address=gmail_address,
                app_password=app_password,
                to_address=notify_to,
                report_path=report_path,
                date_str=date_str,
                top_stocks=recommendations,
            )
            if success:
                logger.info(f"メール送信成功: {notify_to}")
            else:
                logger.error("メール送信失敗")

    logger.info("完了")
    return recommendations, report_path


def main():
    parser = argparse.ArgumentParser(description="株式投資支援ツール")
    parser.add_argument("--top-n", type=int, default=int(os.getenv("TOP_N", 5)), help="推奨銘柄数（デフォルト: 5）")
    parser.add_argument("--dry-run", action="store_true", help="メール送信なしで動作確認")
    args = parser.parse_args()

    run(top_n=args.top_n, dry_run=args.dry_run)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception(f"致命的エラーが発生しました: {e}")
        sys.exit(1)
