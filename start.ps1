$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$snowRoot = Join-Path $projectRoot 'tools\SnowLuma'
$logRoot = Join-Path $projectRoot 'logs'
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null

$snowListening = Get-NetTCPConnection -LocalPort 5099 -State Listen -ErrorAction SilentlyContinue
if (-not $snowListening) {
    $node = Join-Path $snowRoot 'node.exe'
    $entry = Join-Path $snowRoot 'index.mjs'
    if (-not (Test-Path -LiteralPath $node) -or -not (Test-Path -LiteralPath $entry)) {
        throw "SnowLuma 未安装在 $snowRoot"
    }
    Start-Process -FilePath $node -ArgumentList 'index.mjs' -WorkingDirectory $snowRoot `
        -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logRoot 'snowluma.stdout.log') `
        -RedirectStandardError (Join-Path $logRoot 'snowluma.stderr.log') | Out-Null
}

$glmListening = Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue
if (-not $glmListening) {
    $proxyExe = if ($env:QQYM_PROXY_EXE) {
        $env:QQYM_PROXY_EXE
    } else {
        Join-Path (Split-Path -Parent $projectRoot) 'zcode-api\zcode-proxy.exe'
    }
    $proxyRoot = Split-Path -Parent $proxyExe
    if (-not (Test-Path -LiteralPath $proxyExe)) {
        throw "GLM 代理未安装在 $proxyExe"
    }
    Start-Process -FilePath $proxyExe -ArgumentList 'serve' -WorkingDirectory $proxyRoot `
        -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logRoot 'glm.stdout.log') `
        -RedirectStandardError (Join-Path $logRoot 'glm.stderr.log') | Out-Null
}

Set-Location -LiteralPath $projectRoot
$env:PYTHONIOENCODING = 'utf-8'
$python = (Get-Command python.exe -ErrorAction Stop).Source
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$process = Start-Process -FilePath $python -ArgumentList '-m qqym.app --config config.json' `
    -WorkingDirectory $projectRoot -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logRoot "monitor-$stamp.stdout.log") `
    -RedirectStandardError (Join-Path $logRoot "monitor-$stamp.stderr.log") `
    -PassThru -Wait
exit $process.ExitCode
