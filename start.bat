@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo.
echo ========================================
echo  家族お出かけアプリ 起動中...
echo ========================================
echo.

pip install flask anthropic requests --quiet --break-system-packages 2>nul || pip install flask anthropic requests --quiet

echo.
echo ブラウザで開く: http://localhost:5051
echo 終了するには Ctrl+C を押してください
echo.
start "" "http://localhost:5051"
python app.py
pause
