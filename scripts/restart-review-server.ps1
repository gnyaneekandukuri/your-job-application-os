param([int]$DrainTimeoutSeconds = 300)
$ErrorActionPreference = 'Stop'
$productRoot = Split-Path -Parent $PSScriptRoot
$dataRoot = if ($env:JOB_REVIEW_DATA_DIR) { $env:JOB_REVIEW_DATA_DIR } else { Join-Path $productRoot '.local' }
$drainFlag = Join-Path $dataRoot 'maintenance.flag'
$ownsFlag = $false
try {
    if (Test-Path -LiteralPath $drainFlag) { Write-Output 'Restart skipped; maintenance already in progress.'; exit 0 }
    $listener = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
    if ($listener) {
        $serverId = $listener.OwningProcess | Select-Object -Unique -First 1
        $server = Get-CimInstance Win32_Process -Filter "ProcessId=$serverId"
        $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($server.ParentProcessId)"
        $expected = Join-Path $productRoot '.venv\Scripts\python.exe'
        if ($server.CommandLine -notmatch 'uvicorn app.main:app' -or
            ($server.CommandLine -notlike "*$expected*" -and $parent.CommandLine -notlike "*$expected*")) {
            throw 'Port 8000 does not belong to this repository.'
        }
        New-Item -ItemType File -Path $drainFlag -ErrorAction Stop | Out-Null
        $ownsFlag = $true
        $deadline = (Get-Date).AddSeconds($DrainTimeoutSeconds)
        do {
            $activity = Invoke-RestMethod 'http://127.0.0.1:8000/api/maintenance/status' -TimeoutSec 10
            if ($activity.draining -and $activity.safe_to_restart) { break }
            if ((Get-Date) -ge $deadline) { Write-Output 'Restart skipped; drain timed out.'; exit 0 }
            Start-Sleep -Seconds 2
        } while ($true)
        Stop-Process -Id $serverId
        Wait-Process -Id $serverId -Timeout 15 -ErrorAction SilentlyContinue
        if (Get-Process -Id $serverId -ErrorAction SilentlyContinue) { throw 'Listener did not stop within 15 seconds.' }
    }
    Start-Process powershell.exe -WindowStyle Hidden -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File',"`"$(Join-Path $PSScriptRoot 'ensure-review-server.ps1')`""
    $deadline = (Get-Date).AddSeconds(30)
    do {
        try {
            $ready = Invoke-RestMethod 'http://127.0.0.1:8000/api/maintenance/status' -TimeoutSec 2
            if ($ready.safe_to_restart) { break }
        } catch { }
        if ((Get-Date) -ge $deadline) { throw 'Replacement server did not become ready.' }
        Start-Sleep -Seconds 1
    } while ($true)
} catch {
    Write-Output ('Restart skipped; ' + $_.Exception.Message)
} finally {
    if ($ownsFlag) { Remove-Item -LiteralPath $drainFlag -ErrorAction SilentlyContinue }
}
