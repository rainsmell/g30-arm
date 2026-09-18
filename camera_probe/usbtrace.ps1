$ErrorActionPreference = 'Continue'
$root = $PSScriptRoot
$etl = Join-Path $root 'usbtrace.etl'
$csv = Join-Path $root 'usbtrace.csv'
$log = Join-Path $root 'usbtrace.log'
Start-Transcript -Path $log -Force | Out-Null

Remove-Item $etl,$csv -ErrorAction SilentlyContinue
& logman stop usbtrace -ets 2>$null | Out-Null
& logman create trace usbtrace `
    -p Microsoft-Windows-USBVideo 0xffffffffffffffff `
    -p Microsoft-Windows-USB-USBHUB3 0xffffffffffffffff `
    -p Microsoft-Windows-USB-USBXHCI 0xffffffffffffffff `
    -p Microsoft-Windows-USB-UCX 0xffffffffffffffff `
    -o $etl -ets | Out-Null
Write-Host "trace started"
Start-Sleep -Seconds 1
& python.exe (Join-Path $root 'capture_once.py')
Start-Sleep -Seconds 2
& logman stop usbtrace -ets | Out-Null
Write-Host "trace stopped"
if (Test-Path $etl) {
    & tracerpt $etl -o $csv -of CSV -y | Out-Null
    Write-Host "converted to $csv  ($((Get-Item $etl).Length) bytes)"
} else { Write-Host "no etl produced" }
Stop-Transcript | Out-Null
