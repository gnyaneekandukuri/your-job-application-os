$ErrorActionPreference = 'Stop'
$productRoot = Split-Path -Parent $PSScriptRoot
$listener = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    # Fail closed if readiness cannot be verified. Never kill unknown listeners.
    $activity = Invoke-RestMethod 'http://127.0.0.1:8000/api/maintenance/status' -TimeoutSec 10
    if (-not $activity.safe_to_restart) {
        Write-Output ('Restart skipped; active work: ' + ($activity.active -join ', '))
        exit 0
    }
    $serverId = $listener.OwningProcess
    $server = Get-CimInstance Win32_Process -Filter "ProcessId=$serverId"
    $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($server.ParentProcessId)"
    $expected = Join-Path $productRoot '.venv\Scripts\python.exe'
    if ($server.CommandLine -notmatch 'uvicorn app.main:app' -or
        ($server.CommandLine -notlike "*$expected*" -and $parent.CommandLine -notlike "*$expected*")) {
        throw 'Port 8000 does not belong to this repository.'
    }
    $activity = Invoke-RestMethod 'http://127.0.0.1:8000/api/maintenance/status' -TimeoutSec 10
    if (-not $activity.safe_to_restart) { Write-Output 'Restart skipped; work started during verification.'; exit 0 }
    Stop-Process -Id $serverId
    Wait-Process -Id $serverId -Timeout 15 -ErrorAction SilentlyContinue
}
Start-Process powershell.exe -WindowStyle Hidden -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File',"`"$(Join-Path $PSScriptRoot 'ensure-review-server.ps1')`""
