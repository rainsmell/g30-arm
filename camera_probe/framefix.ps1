$ErrorActionPreference='Continue'
$log = Join-Path $PSScriptRoot 'framefix.log'
Start-Transcript -Path $log -Force | Out-Null
Write-Host "restarting FrameServer..."
Restart-Service FrameServer -Force -ErrorAction Continue
Start-Sleep -Seconds 2
$id = 'USB\VID_0C45&PID_636B&MI_00\6&315a9f63&0&0000'
Write-Host "cycling camera device $id"
& pnputil.exe /disable-device "$id" | Out-Null
Start-Sleep -Seconds 2
& pnputil.exe /enable-device "$id" | Out-Null
Start-Sleep -Seconds 3
Write-Host "done"
Stop-Transcript | Out-Null
