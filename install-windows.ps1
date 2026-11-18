$ErrorActionPreference = 'Stop'

$packageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $packageRoot
$installLogPath = Join-Path $packageRoot 'install-result.log'
try {
    Start-Transcript -Path $installLogPath -Append | Out-Null
} catch {
    Write-Warning 'Could not start the install log.'
}

function Test-Python311([string]$command, [bool]$isPyLauncher) {
    try {
        if ($isPyLauncher) {
            & $command -3.11 -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)" 2>$null
        } else {
            & $command -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)" 2>$null
        }
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

Write-Host ''
Write-Host '========================================' -ForegroundColor Cyan
Write-Host '  Wanda Travel Service - Windows setup' -ForegroundColor Cyan
Write-Host '========================================' -ForegroundColor Cyan

$pyLauncher = Get-Command py -ErrorAction SilentlyContinue
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
$venvPath = Join-Path $packageRoot 'venv'

if (Test-Path (Join-Path $venvPath 'Scripts\python.exe')) {
    Write-Host '[OK] Existing local environment found' -ForegroundColor Green
} else {
    if ($pyLauncher -and (Test-Python311 $pyLauncher.Source $true)) {
        Write-Host '[INFO] Creating a local environment with Python 3.11' -ForegroundColor Yellow
        & $pyLauncher.Source -3.11 -m venv $venvPath
    } elseif ($pythonCommand -and (Test-Python311 $pythonCommand.Source $false)) {
        Write-Host '[INFO] Creating a local environment with Python 3.11' -ForegroundColor Yellow
        & $pythonCommand.Source -m venv $venvPath
    } else {
        Write-Host '[ERROR] Python 3.11 was not found.' -ForegroundColor Red
        Write-Host 'Install Python 3.11 and select Add Python to PATH, then run this file again.' -ForegroundColor Yellow
        Write-Host 'Check with: py -3.11 --version' -ForegroundColor Yellow
        Read-Host 'Press Enter to return to PowerShell'
        exit 1
    }
}

$venvPython = Join-Path $venvPath 'Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    throw 'Failed to create the local Python environment.'
}

Write-Host '[INFO] Installing runtime dependencies. This may take a few minutes...' -ForegroundColor Yellow
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'pip upgrade failed.' }
& $venvPython -m pip install -r (Join-Path $packageRoot 'requirements-runtime.txt')
if ($LASTEXITCODE -ne 0) { throw 'Runtime dependency installation failed. Check the network and run this file again.' }

Write-Host '[INFO] Installing browser automation components...' -ForegroundColor Yellow
& $venvPython -m playwright install chromium
if ($LASTEXITCODE -ne 0) {
    Write-Warning 'Chromium download failed. You can still try starting the service if Chrome or Edge is installed.'
}

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Warning 'Node.js was not found. Some JavaScript features require Node.js 16 or later.'
}

New-Item -ItemType Directory -Force -Path (Join-Path $packageRoot 'data') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $packageRoot 'logs') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $packageRoot 'browser_data') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $packageRoot 'trajectory_history') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $packageRoot 'static\uploads\images') | Out-Null

Write-Host ''
Write-Host '[DONE] Installation complete. Double-click start-local.bat to start the service.' -ForegroundColor Green
Write-Host 'Admin page: http://localhost:9000' -ForegroundColor Green
try {
    Stop-Transcript | Out-Null
} catch {
}
Read-Host 'Press Enter to return to PowerShell'
