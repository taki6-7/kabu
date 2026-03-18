@echo off
chcp 65001 > nul
title 株式投資支援ツール

echo ============================================================
echo   株式投資支援ツール
echo ============================================================
echo.

REM --- スクリプトのあるフォルダに移動 ---
cd /d "%~dp0"

REM --- Python存在チェック ---
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [エラー] Python が見つかりません。
    echo         Python をインストールしてください。
    echo         https://www.python.org/downloads/
    goto :END
)

REM --- 依存パッケージのインストール確認 ---
echo [準備] 必要なパッケージを確認しています...
python -m pip install -r requirements.txt --quiet
if %errorlevel% neq 0 (
    echo [エラー] パッケージのインストールに失敗しました。
    echo         インターネット接続を確認してください。
    goto :END
)
echo [準備完了] パッケージの確認が完了しました。
echo.

REM --- ログフォルダを事前作成 ---
if not exist logs mkdir logs

REM --- 実行 ---
python main.py %*
set EXIT_CODE=%errorlevel%

echo.
if %EXIT_CODE% neq 0 (
    echo [エラー] ツールがエラーコード %EXIT_CODE% で終了しました。
    if exist logs\crash.log (
        echo         クラッシュ詳細: logs\crash.log
    ) else (
        echo         詳細は logs\ フォルダのログファイルを参照してください。
    )
) else (
    echo [完了] 正常に終了しました。レポートは reports\ フォルダに保存されています。
)

:END
echo.
echo 何かキーを押すと閉じます...
pause > nul
