$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectRoot

$pythonPath = Join-Path $projectRoot 'venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    Write-Host '[ERROR] Local Python environment not found: venv\Scripts\python.exe' -ForegroundColor Red
    Write-Host 'Run install-windows.bat first.' -ForegroundColor Yellow
    exit 1
}

# Local mode does not use Docker Xvfb/VNC; captcha handling keeps the original flow.
Remove-Item Env:DOCKER_ENV -ErrorAction SilentlyContinue
Remove-Item Env:DISPLAY -ErrorAction SilentlyContinue
$env:USE_XVFB = 'false'
$env:ENABLE_HEADFUL = 'true'
$env:ENABLE_VNC = 'false'
$env:API_HOST = '127.0.0.1'
$env:API_PORT = '9000'
$env:PYTHONUNBUFFERED = '1'

Write-Host ''
Write-Host '========================================' -ForegroundColor Cyan
Write-Host '  Wanda Travel Service - Windows local mode' -ForegroundColor Cyan
Write-Host '========================================' -ForegroundColor Cyan
Write-Host 'Admin page: http://localhost:9000' -ForegroundColor Green
Write-Host 'Captcha handling: use the project original flow' -ForegroundColor Green
Write-Host 'Data directory: .\data (shared with Docker if used)' -ForegroundColor Green
Write-Host 'Press Ctrl+C to stop the local service' -ForegroundColor Yellow
Write-Host ''

& $pythonPath -u 'Start.py'
exit $LASTEXITCODE
