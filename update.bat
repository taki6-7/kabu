@echo off
chcp 65001 > nul
title 株式投資支援ツール - 最新版に更新

echo ============================================================
echo   株式投資支援ツール — 最新版に更新
echo ============================================================
echo.

REM --- スクリプトのあるフォルダに移動 ---
cd /d "%~dp0"

REM --- Git存在チェック ---
where git >nul 2>&1
if %errorlevel% neq 0 (
    echo [エラー] Git が見つかりません。
    echo         Git をインストールしてください。
    echo         https://git-scm.com/downloads
    goto :END
)

REM --- 更新実行 ---
echo 最新のコードを取得しています...
echo.
git pull origin claude/stock-recommendation-tool-RJE6T
set GIT_EXIT=%errorlevel%
echo.

if %GIT_EXIT% neq 0 (
    echo [エラー] 更新に失敗しました。
    echo         ネットワーク接続を確認してください。
    goto :END
)

echo [成功] 最新版に更新しました。
echo.

REM --- pip依存関係も更新 ---
where python >nul 2>&1
if %errorlevel% equ 0 (
    echo 依存パッケージを確認しています...
    python -m pip install -r requirements.txt --quiet
    if %errorlevel% equ 0 (
        echo [成功] 依存パッケージも最新です。
    ) else (
        echo [警告] パッケージ更新に問題がありました。動作に支障がある場合はお知らせください。
    )
)

:END
echo.
echo 何かキーを押すと閉じます...
pause > nul
