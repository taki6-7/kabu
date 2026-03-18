@echo off
chcp 65001 > nul

REM --- スクリプトのあるフォルダに移動 ---
cd /d "%~dp0"

REM --- ログフォルダを事前作成 ---
if not exist logs mkdir logs

REM --- タイムスタンプ取得 ---
for /f "tokens=1-3 delims=/ " %%a in ('date /t') do set TODAY=%%a%%b%%c
set LOGFILE=logs\scheduler_%TODAY%.log

echo [%date% %time%] タスクスケジューラ起動 >> "%LOGFILE%"

REM --- Python存在チェック ---
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [%date% %time%] [エラー] Python が見つかりません >> "%LOGFILE%"
    exit /b 1
)

REM --- 依存パッケージの確認（ログに出力）---
python -m pip install -r requirements.txt --quiet >> "%LOGFILE%" 2>&1
if %errorlevel% neq 0 (
    echo [%date% %time%] [エラー] パッケージインストール失敗 >> "%LOGFILE%"
    exit /b 1
)

REM --- メイン実行 ---
echo [%date% %time%] 実行開始 >> "%LOGFILE%"
python main.py >> "%LOGFILE%" 2>&1
set EXIT_CODE=%errorlevel%

if %EXIT_CODE% neq 0 (
    echo [%date% %time%] [エラー] 終了コード %EXIT_CODE% >> "%LOGFILE%"
) else (
    echo [%date% %time%] [完了] 正常終了 >> "%LOGFILE%"
)

exit /b %EXIT_CODE%
