# Temporarily detach Huawei ccfltr (AI Camera filter) from the Camera class upper filters,
# then restart the target camera device so the driver stack is rebuilt.
# Fully reversible: run ccfltr_restore.ps1 afterwards.
$ErrorActionPreference = 'Stop'
$log = Join-Path $PSScriptRoot 'ccfltr_off.log'
Start-Transcript -Path $log -Force | Out-Null

$cls   = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{ca3e7ab9-b4c3-4ae6-8251-579ef933890f}'
$bak   = Join-Path $env:TEMP 'ccfltr_upperfilters.bak'
$targetVid = 'VID_0C45'

$cur = (Get-ItemProperty -Path $cls).UpperFilters
$cur | Set-Content -Path $bak -Encoding UTF8
Write-Host "backup UpperFilters -> $bak : $($cur -join ',')"

$new = @($cur | Where-Object { $_ -ne 'ccfltr' })
Set-ItemProperty -Path $cls -Name UpperFilters -Value $new -Type MultiString
Write-Host "new UpperFilters: $($new -join ',')"

$devs = Get-PnpDevice -Class Camera -PresentOnly | Where-Object { $_.InstanceId -like "*$targetVid*" }
if (-not $devs) { Write-Host "target camera not found"; exit 0 }
foreach ($d in $devs) {
    Write-Host "restarting $($d.InstanceId)"
    & pnputil.exe /disable-device "$($d.InstanceId)" | Out-Null
    Start-Sleep -Seconds 2
    & pnputil.exe /enable-device "$($d.InstanceId)" | Out-Null
    Start-Sleep -Seconds 2
}
Write-Host "done. now test the camera."
Stop-Transcript | Out-Null
Set-Content -Path (Join-Path $PSScriptRoot "ccfltr_off.result") -Value "OK"
