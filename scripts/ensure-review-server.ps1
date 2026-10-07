$ErrorActionPreference = 'Stop'
$productRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $productRoot
# A listening server may be busy. Never kill it or launch a competing instance.
$probe = New-Object System.Net.Sockets.TcpClient
try {
    $attempt = $probe.ConnectAsync('127.0.0.1', 8000)
    try { [void]$attempt.Wait(2000) } catch { }
    if ($probe.Connected) { exit 0 }
} finally { $probe.Dispose() }
New-Item -ItemType Directory -Force -Path '.local' | Out-Null
$logPath = Join-Path $productRoot '.local/server-supervisor.log'
Add-Content -LiteralPath $logPath -Value "$(Get-Date -Format o) Starting local server from Windows Task Scheduler."
try {
    & (Join-Path $PSScriptRoot 'start-review.ps1') *>> $logPath
} catch {
    Add-Content -LiteralPath $logPath -Value "$(Get-Date -Format o) Server stopped: $($_.Exception.Message)"
    exit 1
}
