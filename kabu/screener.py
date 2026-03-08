"""
銘柄スクリーニングモジュール
東証プライム銘柄を流動性でフィルタリングして候補銘柄を絞り込む
"""

import logging
import time
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

# 東証プライム主要銘柄（時価総額上位500銘柄程度を代表するリスト）
# 実運用ではJPXのCSVを取得して使うことを推奨
PRIME_TICKERS_SAMPLE = [
    # 大型株
    "7203.T", "6758.T", "6861.T", "8306.T", "9432.T",
    "9433.T", "4502.T", "6954.T", "8316.T", "7974.T",
    "9984.T", "6501.T", "6702.T", "6752.T", "7267.T",
    "7269.T", "4661.T", "8411.T", "9020.T", "9022.T",
    # 中型株
    "4063.T", "4519.T", "6367.T", "6645.T", "6723.T",
    "6857.T", "6902.T", "7011.T", "7201.T", "7751.T",
    "8001.T", "8002.T", "8031.T", "8058.T", "8267.T",
    "8801.T", "8802.T", "9201.T", "9602.T", "9613.T",
    "2914.T", "3382.T", "4452.T", "4689.T", "4755.T",
    "5401.T", "5108.T", "6098.T", "6146.T", "6471.T",
    "6503.T", "6594.T", "6762.T", "6920.T", "7309.T",
    "7733.T", "7741.T", "7832.T", "8035.T", "8309.T",
    "8604.T", "8766.T", "9005.T", "9007.T", "9437.T",
    "2503.T", "2802.T", "3861.T", "4568.T", "4578.T",
    "6301.T", "6326.T", "6506.T", "6674.T", "6701.T",
    "6724.T", "6770.T", "6841.T", "7735.T", "7762.T",
    "7912.T", "8304.T", "8331.T", "8354.T", "8601.T",
    "8725.T", "9064.T", "9104.T", "9107.T", "9301.T",
    "1925.T", "1928.T", "2768.T", "3289.T", "4324.T",
    "4543.T", "5020.T", "5714.T", "6305.T", "6479.T",
]


def get_prime_tickers() -> list[str]:
    """東証プライム銘柄リストを返す（将来JPX CSVに差し替え可能）"""
    return PRIME_TICKERS_SAMPLE


def apply_liquidity_filter(
    tickers: list[str],
    min_volume: int = 500_000,
    min_market_cap: float = 50_000_000_000,
    batch_size: int = 20,
) -> list[str]:
    """
    流動性フィルター：出来高・時価総額で候補を絞り込む

    Args:
        tickers: 対象銘柄リスト
        min_volume: 最低平均出来高（株数）
        min_market_cap: 最低時価総額（円）
        batch_size: yfinance一括取得のバッチサイズ

    Returns:
        フィルター通過銘柄リスト
    """
    passed = []
    total = len(tickers)

    for i in range(0, total, batch_size):
        batch = tickers[i:i + batch_size]
        logger.info(f"流動性フィルター: {i+1}〜{min(i+batch_size, total)}/{total}銘柄処理中...")

        try:
            data = yf.download(
                batch,
                period="20d",
                auto_adjust=True,
                progress=False,
                group_by="ticker",
            )

            for ticker in batch:
                try:
                    if len(batch) == 1:
                        volume_series = data["Volume"]
                    else:
                        volume_series = data[ticker]["Volume"]

                    avg_volume = volume_series.dropna().mean()
                    if avg_volume < min_volume:
                        continue

                    info = yf.Ticker(ticker).fast_info
                    market_cap = getattr(info, "market_cap", 0) or 0
                    if market_cap < min_market_cap:
                        continue

                    passed.append(ticker)

                except Exception as e:
                    logger.debug(f"{ticker} スキップ: {e}")

            time.sleep(0.5)

        except Exception as e:
            logger.warning(f"バッチ取得エラー: {e}")

    logger.info(f"流動性フィルター通過: {len(passed)}/{total}銘柄")
    return passed
