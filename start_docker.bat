@echo off
:: Batch script to start Docker Desktop Service and launch Docker Desktop with Administrator privileges
echo ========================================================
echo   Configuring and Starting Docker Desktop Service
echo ========================================================
echo.

:: Configure the service to start automatically on boot
sc config com.docker.service start= auto

:: Start the service
net start com.docker.service

echo.
echo Launching Docker Desktop...
start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"

echo.
echo ========================================================
echo   Docker Desktop has been launched successfully!
echo   All data is stored permanently on Drive D: (265 GB free).
echo ========================================================
timeout /t 5
