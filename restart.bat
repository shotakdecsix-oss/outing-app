@echo off
chcp 65001 > nul
echo ========================================
echo  古いPythonプロセスを終了中...
echo ========================================
taskkill /F /IM python.exe 2>nul
timeout /t 2 /nobreak > nul

echo.
echo ========================================
echo  お出かけアプリを起動中...
echo ========================================
echo.
cd /d "%~dp0"
pip install flask anthropic requests --quiet --break-system-packages 2>nul || pip install flask anthropic requests --quiet
echo.
echo ブラウザで開く: http://localhost:5051
echo.
start "" "http://localhost:5051"
python app.py
pause
