@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
echo ========================================
echo   Wanda Travel Service - Windows setup
echo ========================================
echo.
echo Checking Python 3.11 and installing dependencies...
echo The PowerShell window will stay open so you can read any error.
echo A log will be saved as install-result.log in this folder.
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -NoExit -File "%~dp0install-windows.ps1"
