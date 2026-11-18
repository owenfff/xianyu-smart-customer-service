[CmdletBinding()]
param(
    [string]$OutputDirectory = '',
    [switch]$IncludeKnowledgeBase
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectRoot

if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $projectRoot 'release'
}

$packageName = if ($IncludeKnowledgeBase) { '万达旅游客服-精简版-带知识库' } else { '万达旅游客服-精简版' }
$packageDirectory = Join-Path $OutputDirectory $packageName
$zipPath = Join-Path $OutputDirectory ($packageName + '.zip')

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
if (Test-Path -LiteralPath $packageDirectory) {
    Remove-Item -LiteralPath $packageDirectory -Recurse -Force
}
if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}
New-Item -ItemType Directory -Force -Path $packageDirectory | Out-Null

$excludedTopLevel = @(
    'debug_manual_cookie_slider.py',
    'debug_manual_password_login.py',
    'release_precheck.py',
    'generate_update_manifest.py',
    'secure_confirm_decrypted.py',
    'secure_freeshipping_decrypted.py',
    'auto_rate_task.py',
    'auto_red_flower_task.py',
    'order_status_handler.py',
    'item_polish_module.py',
    'prepare_package_database.py'
)

Get-ChildItem -LiteralPath $projectRoot -File -Filter '*.py' |
    Where-Object { $excludedTopLevel -notcontains $_.Name } |
    Copy-Item -Destination $packageDirectory -Force

$requiredFiles = @(
    'global_config.yml',
    'announcement.json',
    'captcha_control.html',
    'requirements-runtime.txt',
    'start-local.bat',
    'start-local.ps1',
    'install-windows.bat',
    'install-windows.ps1',
    'PACKAGE_README.txt',
    'PACKAGE_README_KB.txt',
    'LICENSE'
)
foreach ($fileName in $requiredFiles) {
    $source = Join-Path $projectRoot $fileName
    if (-not (Test-Path -LiteralPath $source)) {
        throw "缺少打包文件：$fileName"
    }
    Copy-Item -LiteralPath $source -Destination $packageDirectory -Force
}

Copy-Item -LiteralPath (Join-Path $projectRoot 'utils') -Destination $packageDirectory -Recurse -Force
Copy-Item -LiteralPath (Join-Path $projectRoot 'static') -Destination $packageDirectory -Recurse -Force

# 运行包不带开发缓存、现有登录状态、历史日志和上传内容。
@(
    (Join-Path $packageDirectory 'utils\__pycache__'),
    (Join-Path $packageDirectory 'static\uploads'),
    (Join-Path $packageDirectory 'utils\item_publisher.py'),
    (Join-Path $packageDirectory 'utils\order_history_sync.py')
) | ForEach-Object {
    if (Test-Path -LiteralPath $_) {
        Remove-Item -LiteralPath $_ -Recurse -Force
    }
}

New-Item -ItemType Directory -Force -Path (Join-Path $packageDirectory 'data') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $packageDirectory 'logs') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $packageDirectory 'browser_data') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $packageDirectory 'trajectory_history') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $packageDirectory 'static\uploads\images') | Out-Null

$readmeSource = if ($IncludeKnowledgeBase) { 'PACKAGE_README_KB.txt' } else { 'PACKAGE_README.txt' }
$unusedReadme = if ($IncludeKnowledgeBase) { 'PACKAGE_README.txt' } else { 'PACKAGE_README_KB.txt' }
$readmeSourcePath = Join-Path $packageDirectory $readmeSource
$unusedReadmePath = Join-Path $packageDirectory $unusedReadme
Remove-Item -LiteralPath $unusedReadmePath -Force

if ($IncludeKnowledgeBase) {
    $pythonCandidates = @(
        (Join-Path $projectRoot 'venv\Scripts\python.exe'),
        'python'
    )
    $pythonCommand = $pythonCandidates | Where-Object {
        if ($_ -eq 'python') { $true } else { Test-Path -LiteralPath $_ }
    } | Select-Object -First 1
    if (-not $pythonCommand) {
        throw '未找到 Python，无法生成带知识库的数据库。'
    }

    $databaseBuilder = Join-Path $projectRoot 'prepare_package_database.py'
    $sourceDatabase = Join-Path $projectRoot 'data\xianyu_data.db'
    $targetDatabase = Join-Path $packageDirectory 'data\xianyu_data.db'
    & $pythonCommand $databaseBuilder --source $sourceDatabase --target $targetDatabase
    if ($LASTEXITCODE -ne 0) {
        throw '带知识库数据库生成失败。'
    }
}

$readmeTarget = Join-Path $packageDirectory '使用说明.txt'
Move-Item -LiteralPath $readmeSourcePath -Destination $readmeTarget -Force

Compress-Archive -Path (Join-Path $packageDirectory '*') -DestinationPath $zipPath -CompressionLevel Optimal

$fileCount = (Get-ChildItem -LiteralPath $packageDirectory -Recurse -File | Measure-Object).Count
$packageSize = [math]::Round(((Get-Item -LiteralPath $zipPath).Length / 1MB), 2)
Write-Host ''
Write-Host "[完成] 精简运行包已生成：$zipPath" -ForegroundColor Green
Write-Host "文件数：$fileCount；压缩包大小：$packageSize MB" -ForegroundColor Green
if ($IncludeKnowledgeBase) {
    Write-Host '已包含独立 RAG 知识库；账号、Cookie、商品、API密钥、Webhook及历史记录未包含。' -ForegroundColor Yellow
} else {
    Write-Host '该包不包含 data、logs、browser_data 和 trajectory_history 中的现有数据。' -ForegroundColor Yellow
}
