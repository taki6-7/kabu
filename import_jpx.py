"""
JPX銘柄一覧Excelを手動インポートするスクリプト。

自動ダウンロードが失敗する場合:
1. ブラウザで以下URLを開きdata_j.xlsをダウンロード
   https://www.jpx.co.jp/markets/statistics-equities/misc/01.html
2. ダウンロードしたファイルをこのスクリプトと同じフォルダに置く
3. python import_jpx.py data_j.xls を実行

Usage:
    python import_jpx.py <xlsファイルパス>
    python import_jpx.py data_j.xls
"""

import sys
import io
import logging
import pandas as pd
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).parent / "kabu" / "data"
_CACHE_FILE = _DATA_DIR / "tickers_prime.csv"


def import_from_file(xls_path: str) -> None:
    path = Path(xls_path)
    if not path.exists():
        logger.error(f"ファイルが見つかりません: {path}")
        sys.exit(1)

    logger.info(f"読み込み中: {path}")
    df_raw = pd.read_excel(path, dtype=str)
    logger.info(f"列名: {df_raw.columns.tolist()}")
    logger.info(f"行数: {len(df_raw)}")

    # 市場区分列・コード列を特定
    market_col = None
    code_col = None
    for col in df_raw.columns:
        col_str = str(col).strip()
        if ("市場" in col_str and "商品" in col_str) or col_str == "市場区分":
            market_col = col
        elif "市場" in col_str and market_col is None:
            market_col = col
        if "コード" in col_str:
            code_col = col

    if market_col is None or code_col is None:
        cols = df_raw.columns.tolist()
        code_col = cols[1] if len(cols) > 1 else cols[0]
        market_col = cols[3] if len(cols) > 3 else cols[2]
        logger.warning(f"列を位置で推定: コード='{code_col}', 市場区分='{market_col}'")

    logger.info(f"使用列: コード='{code_col}', 市場区分='{market_col}'")

    # プライム市場でフィルタ
    prime_mask = df_raw[market_col].str.contains("プライム", na=False)
    df_prime = df_raw[prime_mask].copy()
    logger.info(f"プライム市場銘柄数: {len(df_prime)}")

    if len(df_prime) == 0:
        logger.error("プライム市場銘柄が抽出できませんでした。")
        logger.error("市場区分列の値サンプル:")
        logger.error(df_raw[market_col].dropna().unique()[:10].tolist())
        sys.exit(1)

    codes = df_prime[code_col].str.strip().str.zfill(4)
    codes = codes[codes.str.match(r"^\d{4}$")]
    tickers = (codes + ".T").tolist()

    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"ticker": tickers}).to_csv(_CACHE_FILE, index=False)
    logger.info(f"保存完了: {_CACHE_FILE}")
    logger.info(f"取得銘柄数: {len(tickers)}")
    logger.info(f"先頭5件: {tickers[:5]}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    import_from_file(sys.argv[1])
