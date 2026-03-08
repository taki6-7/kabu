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

REM --- 実行 ---
python main.py %*
set EXIT_CODE=%errorlevel%

echo.
if %EXIT_CODE% neq 0 (
    echo [エラー] ツールがエラーコード %EXIT_CODE% で終了しました。
    echo         詳細は logs\ フォルダのログファイルを参照してください。
) else (
    echo [完了] 正常に終了しました。レポートは reports\ フォルダに保存されています。
)

:END
echo.
echo 何かキーを押すと閉じます...
pause > nul
