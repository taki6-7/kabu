@echo off
chcp 65001 > nul
echo 株式投資支援ツール を起動します...
echo.

python main.py %*
set EXIT_CODE=%errorlevel%

echo.
if %EXIT_CODE% neq 0 (
    echo [エラー] ツールがエラーコード %EXIT_CODE% で終了しました。
    echo         上記のログメッセージを確認してください。
    echo         詳細は logs\ フォルダのログファイルを参照してください。
) else (
    echo [完了] 正常に終了しました。レポートは reports\ フォルダに保存されています。
)

echo.
pause
