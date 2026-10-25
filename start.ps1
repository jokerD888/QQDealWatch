$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$snowRoot = Join-Path $projectRoot 'tools\SnowLuma'
$logRoot = Join-Path $projectRoot 'logs'
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null

function Start-SnowLumaIfNeeded {
    $snowListening = Get-NetTCPConnection -LocalPort 5099 -State Listen -ErrorAction SilentlyContinue
    if ($snowListening) { return }
    $node = Join-Path $snowRoot 'node.exe'
    $entry = Join-Path $snowRoot 'index.mjs'
    if (-not (Test-Path -LiteralPath $node) -or -not (Test-Path -LiteralPath $entry)) {
        throw "SnowLuma 未安装在 $snowRoot"
    }
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    Start-Process -FilePath $node -ArgumentList 'index.mjs' -WorkingDirectory $snowRoot `
        -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logRoot "snowluma-$stamp.stdout.log") `
        -RedirectStandardError (Join-Path $logRoot "snowluma-$stamp.stderr.log") | Out-Null
}

$proxyExe = if ($env:QQYM_PROXY_EXE) {
    $env:QQYM_PROXY_EXE
} else {
    Join-Path (Split-Path -Parent $projectRoot) 'zcode-api\zcode-proxy.exe'
}

function Start-GlmIfNeeded {
    $glmListening = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
    if ($glmListening -or -not (Test-Path -LiteralPath $proxyExe)) { return }
    $existing = @(Get-CimInstance Win32_Process -Filter "Name='zcode-proxy.exe'" |
        Where-Object { $_.ExecutablePath -eq $proxyExe })
    if ($existing.Count -gt 0) { return }
    $proxyRoot = Split-Path -Parent $proxyExe
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    Start-Process -FilePath $proxyExe -ArgumentList 'serve' -WorkingDirectory $proxyRoot `
        -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logRoot "glm-$stamp.stdout.log") `
        -RedirectStandardError (Join-Path $logRoot "glm-$stamp.stderr.log") | Out-Null
}

Start-SnowLumaIfNeeded
Start-GlmIfNeeded

Set-Location -LiteralPath $projectRoot
$env:PYTHONIOENCODING = 'utf-8'
$python = (Get-Command python.exe -ErrorAction Stop).Source
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$process = Start-Process -FilePath $python -ArgumentList '-m qqym.app --config config.json' `
    -WorkingDirectory $projectRoot -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logRoot "monitor-$stamp.stdout.log") `
    -RedirectStandardError (Join-Path $logRoot "monitor-$stamp.stderr.log") `
    -PassThru

while (-not $process.HasExited) {
    Start-Sleep -Seconds 30
    Start-SnowLumaIfNeeded
    Start-GlmIfNeeded
    $process.Refresh()
}

exit $process.ExitCode
