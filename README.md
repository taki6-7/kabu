# kabu — 株式投資支援ツール

東証プライム銘柄をテクニカル・ファンダメンタル分析でスコアリングし、翌営業日の推奨銘柄をHTMLレポートとして生成・メール送信するツールです。

## ディレクトリ構成

```
kabu/
├── main.py              # メインスクリプト
├── requirements.txt     # 依存パッケージ
├── .env.example         # 環境変数テンプレート
├── logs/                # 実行ログ（自動生成）
├── reports/             # HTMLレポート出力先（自動生成）
└── kabu/
    ├── screener.py      # 銘柄スクリーニング
    ├── technical.py     # テクニカルスコアリング
    ├── fundamental.py   # ファンダメンタルスコアリング
    ├── reporter.py      # HTMLレポート生成
    └── notifier.py      # Gmail送信
```

> **注意**: `logs/` と `reports/` フォルダは初回実行時に自動で作成されます。リポジトリをクローンした直後は `.gitkeep` ファイルのみが存在します。

## セットアップ

### 1. 依存パッケージのインストール

```bash
pip install -r requirements.txt
```

### 2. 環境変数の設定

`.env.example` をコピーして `.env` を作成し、必要な値を入力してください。

```bash
cp .env.example .env
```

`.env` の設定項目:

| 変数名 | 説明 | 例 |
|--------|------|----|
| `GMAIL_ADDRESS` | 送信元Gmailアドレス | `your@gmail.com` |
| `GMAIL_APP_PASSWORD` | Googleアプリパスワード | `xxxx xxxx xxxx xxxx` |
| `NOTIFY_TO` | 送信先メールアドレス | `recipient@example.com` |
| `TOP_N` | 推奨銘柄数（デフォルト: 5） | `5` |
| `MIN_MARKET_CAP` | 最低時価総額（円、デフォルト: 500億） | `50000000000` |
| `MIN_VOLUME` | 最低出来高（デフォルト: 50万株） | `500000` |

## 使い方

スクリプトはどのディレクトリからでも実行できます。`logs/` と `reports/` は **スクリプトと同じフォルダ** に自動作成されます。

```bash
# 通常実行（メール送信あり）
python main.py

# メール送信なしで動作確認
python main.py --dry-run

# 推奨銘柄数を変更（例: 上位3銘柄）
python main.py --top-n 3
```

## 出力

- **HTMLレポート**: `reports/report_YYYYMMDD_HHMM.html`
- **実行ログ**: `logs/run_YYYYMMDD.log`

## スコアリング基準

| カテゴリ | 満点 | 指標 |
|----------|------|------|
| トレンド | 15点 | 5MA > 25MA > 75MA（パーフェクトオーダー） |
| モメンタム | 15点 | RSI・MACDゴールデンクロス |
| 出来高 | 15点 | 直近出来高が20日平均比 |
| 値動き | 15点 | 陽線・52週高値水準・ATR |
| 米国連動 | 10点 | 関連セクターETFの前日リターン |
| バリュエーション | 5点 | PER・PBRが業種平均以下 |
| 業績トレンド | 10点 | 売上/利益成長率・ROE |
| **合計** | **100点** | |

## 免責事項

本ツールは情報提供のみを目的としており、投資勧誘を意図するものではありません。投資判断はご自身の責任において行ってください。
