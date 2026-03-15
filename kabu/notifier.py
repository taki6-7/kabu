"""
Gmail通知モジュール
HTMLレポートをメール本文として送信する
"""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path

logger = logging.getLogger(__name__)


def send_report_email(
    gmail_address: str,
    app_password: str,
    to_address: str,
    report_path: str,
    date_str: str,
    top_stocks: list[dict],
) -> bool:
    """
    HTMLレポートをGmailで送信する

    Args:
        gmail_address: 送信元Gmailアドレス
        app_password: Googleアプリパスワード（16文字）
        to_address: 送信先アドレス
        report_path: HTMLレポートファイルパス
        date_str: レポート日付文字列
        top_stocks: 推奨銘柄リスト（件名用）

    Returns:
        送信成功: True / 失敗: False
    """
    # アプリパスワードのスペースを除去（Googleは4文字区切りで表示するため）
    app_password = app_password.replace(" ", "")

    try:
        msg = MIMEMultipart("mixed")
        msg["Subject"] = f"【株式推奨】{date_str} 本日の注目銘柄 TOP5"
        msg["From"] = gmail_address
        msg["To"] = to_address

        # メール本文（プレーンテキスト + HTML を alternative でラップ）
        tickers_str = " / ".join(
            f"{r.get('name', r['ticker'])}({r['ticker']})"
            for r in top_stocks[:5]
        )
        body_text = f"""
株式投資支援ツール — 本日の推奨銘柄レポート
============================================
日付: {date_str}
推奨銘柄: {tickers_str}

詳細はHTMLレポート（添付ファイル）をご確認ください。

---
本メールはシステムにより自動送信されました。
投資判断はご自身の責任において行ってください。
""".strip()

        alt_part = MIMEMultipart("alternative")
        alt_part.attach(MIMEText(body_text, "plain", "utf-8"))

        # HTMLレポートを添付 & インライン本文
        report_file = Path(report_path)
        if report_file.exists():
            with open(report_file, "r", encoding="utf-8") as f:
                html_content = f.read()
            alt_part.attach(MIMEText(html_content, "html", "utf-8"))

        msg.attach(alt_part)

        if report_file.exists():
            with open(report_file, "rb") as f:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition",
                f'attachment; filename="{report_file.name}"',
            )
            msg.attach(part)

        # Gmail SMTP送信
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_address, app_password)
            server.send_message(msg)

        logger.info(f"メール送信完了: {to_address}")
        return True

    except smtplib.SMTPAuthenticationError:
        logger.error("Gmail認証エラー: アドレスまたはアプリパスワードを確認してください")
        return False
    except smtplib.SMTPException as e:
        logger.error(f"SMTP送信エラー: {e}")
        return False
    except Exception as e:
        logger.error(f"メール送信エラー: {e}")
        return False
