# Restore the Camera class upper filters that ccfltr_off.ps1 backed up, then restart cameras.
$ErrorActionPreference = 'Stop'

$cls = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{ca3e7ab9-b4c3-4ae6-8251-579ef933890f}'
$bak = Join-Path $env:TEMP 'ccfltr_upperfilters.bak'

if (-not (Test-Path $bak)) { Write-Host "no backup found at $bak"; exit 1 }
$orig = @(Get-Content -Path $bak | Where-Object { $_ -ne '' })
Set-ItemProperty -Path $cls -Name UpperFilters -Value $orig -Type MultiString
Write-Host "restored UpperFilters: $($orig -join ',')"

foreach ($d in (Get-PnpDevice -Class Camera -PresentOnly)) {
    & pnputil.exe /disable-device "$($d.InstanceId)" | Out-Null
    Start-Sleep -Seconds 1
    & pnputil.exe /enable-device "$($d.InstanceId)" | Out-Null
    Start-Sleep -Seconds 1
}
Write-Host "done."
