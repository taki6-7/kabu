"""
銘柄スクリーニングモジュール
東証プライム銘柄を流動性でフィルタリングして候補銘柄を絞り込む
"""

import logging
import time
import datetime
from pathlib import Path
import io

import pandas as pd
import requests
import yfinance as yf

logger = logging.getLogger(__name__)

# JPX公式CSVのURL（東証上場銘柄一覧 data_j.xls）
JPX_XLS_URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"

# キャッシュファイルパス
_DATA_DIR = Path(__file__).parent / "data"
_CACHE_FILE = _DATA_DIR / "tickers_prime.csv"

# フォールバック用サンプル（JPX取得失敗時）
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


def _is_cache_fresh() -> bool:
    """キャッシュが今日のものかチェック"""
    if not _CACHE_FILE.exists():
        return False
    mtime = datetime.date.fromtimestamp(_CACHE_FILE.stat().st_mtime)
    return mtime >= datetime.date.today()


def _parse_jpx_excel(content: bytes) -> list[str]:
    """JPX Excelバイト列を解析してプライム銘柄ティッカーリストを返す"""
    df_raw = pd.read_excel(io.BytesIO(content), dtype=str)
    logger.debug(f"JPX Excel列名: {df_raw.columns.tolist()}")

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

    prime_mask = df_raw[market_col].str.contains("プライム", na=False)
    df_prime = df_raw[prime_mask].copy()
    logger.info(f"プライム市場銘柄数: {len(df_prime)}")

    if len(df_prime) == 0:
        raise ValueError(
            f"プライム市場銘柄が抽出できませんでした。"
            f"市場区分の値: {df_raw[market_col].dropna().unique()[:5].tolist()}"
        )

    codes = df_prime[code_col].str.strip().str.zfill(4)
    codes = codes[codes.str.match(r"^\d{4}$")]
    return (codes + ".T").tolist()


def _find_local_jpx_file() -> Path | None:
    """data_j.xls をよくある場所から自動検索する"""
    home = Path.home()
    search_dirs = [
        Path.cwd(),                          # カレントディレクトリ
        Path(__file__).parent.parent,        # プロジェクトルート
        home / "Downloads",                  # ダウンロード
        home / "Desktop",                    # デスクトップ
        home / "Documents",                  # ドキュメント
        home / "OneDrive" / "Downloads",     # OneDrive Downloads
        home / "OneDrive" / "デスクトップ",
        home / "デスクトップ",
        home / "ダウンロード",
        home / "ドキュメント",
    ]
    for d in search_dirs:
        for name in ("data_j.xls", "data_j.xlsx", "data_j (1).xls"):
            candidate = d / name
            if candidate.exists():
                logger.info(f"data_j.xls を発見: {candidate}")
                return candidate
    return None


def fetch_prime_tickers_from_jpx() -> list[str]:
    """
    JPX公式Excelから東証プライム銘柄コードを取得する。
    優先順位:
      1. 当日キャッシュ
      2. JPX自動ダウンロード
      3. ローカルのdata_j.xlsを自動検索
      4. 古いキャッシュ（期限切れでも使用）
    """
    _DATA_DIR.mkdir(parents=True, exist_ok=True)

    # 1. 当日キャッシュ
    if _is_cache_fresh():
        try:
            df = pd.read_csv(_CACHE_FILE, dtype=str)
            tickers = df["ticker"].tolist()
            logger.info(f"JPXキャッシュから{len(tickers)}銘柄をロード")
            return tickers
        except Exception as e:
            logger.warning(f"キャッシュ読み込み失敗: {e}")

    # 2. JPX自動ダウンロード
    logger.info("JPX公式サイトから銘柄一覧をダウンロード中...")
    try:
        session = requests.Session()
        page_url = "https://www.jpx.co.jp/markets/statistics-equities/misc/01.html"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
        }
        try:
            session.get(page_url, headers=headers, timeout=15)
        except Exception:
            pass

        resp = session.get(JPX_XLS_URL, headers={**headers, "Referer": page_url}, timeout=60)
        resp.raise_for_status()
        if len(resp.content) < 1000:
            raise ValueError(f"コンテンツが小さすぎます ({len(resp.content)} bytes)")

        tickers = _parse_jpx_excel(resp.content)
        pd.DataFrame({"ticker": tickers}).to_csv(_CACHE_FILE, index=False)
        logger.info(f"JPXから{len(tickers)}銘柄を取得・キャッシュ保存")
        return tickers

    except Exception as e:
        logger.warning(f"JPX自動ダウンロード失敗: {e}")

    # 3. ローカルのdata_j.xlsを自動検索
    local_file = _find_local_jpx_file()
    if local_file:
        try:
            tickers = _parse_jpx_excel(local_file.read_bytes())
            pd.DataFrame({"ticker": tickers}).to_csv(_CACHE_FILE, index=False)
            logger.info(f"ローカルファイルから{len(tickers)}銘柄を取得・キャッシュ保存: {local_file}")
            return tickers
        except Exception as e:
            logger.warning(f"ローカルファイル読み込み失敗: {e}")

    # 4. 古いキャッシュ（期限切れでも使用）
    if _CACHE_FILE.exists():
        try:
            df = pd.read_csv(_CACHE_FILE, dtype=str)
            tickers = df["ticker"].tolist()
            logger.warning(f"古いキャッシュを使用: {len(tickers)}銘柄")
            return tickers
        except Exception:
            pass

    logger.error(
        "JPX銘柄リスト取得失敗。対処法:\n"
        "  ブラウザで https://www.jpx.co.jp/markets/statistics-equities/misc/01.html を開き\n"
        "  「東証上場銘柄一覧」のdata_j.xlsをダウンロードフォルダに保存してください。\n"
        "  次回実行時に自動検出されます。"
    )
    return []


def get_prime_tickers() -> list[str]:
    """
    東証プライム銘柄リストを返す。
    JPX公式CSVから取得し、失敗時はサンプルリストにフォールバック。
    """
    tickers = fetch_prime_tickers_from_jpx()
    if tickers:
        return tickers

    logger.warning(
        f"JPX取得失敗。フォールバック: サンプル{len(PRIME_TICKERS_SAMPLE)}銘柄を使用"
    )
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
