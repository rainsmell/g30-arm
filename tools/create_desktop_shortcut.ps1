# Create (or refresh) a Desktop shortcut for the Robot Arm Web Console.
# Usage:  powershell -ExecutionPolicy Bypass -File tools\create_desktop_shortcut.ps1
param(
    [string]$Name = "机械臂控制台",
    [switch]$NoIcon
)

$ErrorActionPreference = "Stop"

$Root     = Split-Path -Parent $PSScriptRoot          # project root
$Target   = Join-Path $Root "启动机械臂Web控制台.bat"
$Desktop  = [Environment]::GetFolderPath("Desktop")
$LinkPath = Join-Path $Desktop ($Name + ".lnk")

if (-not (Test-Path $Target)) { throw "Launcher not found: $Target" }

# --- vector icon (tools/robot_arm.ico, drawn by tools/make_icon.py) --------
$IconPath = Join-Path $Root "tools\robot_arm.ico"
if (-not $NoIcon -and -not (Test-Path $IconPath)) {
    $py = Get-Command python -ErrorAction SilentlyContinue
    if ($py) {
        try {
            & $py.Source (Join-Path $Root "tools\make_icon.py") | Out-Null
        } catch {
            Write-Host "[WARN] icon generation skipped: $($_.Exception.Message)"
        }
    }
}
if (-not (Test-Path $IconPath)) { $IconPath = $null }

# Windows keeps a per-path icon cache, so pointing the .lnk at the same file
# after regenerating it often still shows the OLD glyph.  Copy the icon to a
# content-hashed filename: a changed icon means a never-seen path, which the
# shell always renders fresh.
$IconRef = $null
if ($IconPath) {
    try {
        $cacheDir = Join-Path $env:LOCALAPPDATA "RobotArmConsole"
        New-Item -ItemType Directory -Force -Path $cacheDir | Out-Null
        $hash = (Get-FileHash $IconPath -Algorithm SHA256).Hash.Substring(0, 12).ToLower()
        $dest = Join-Path $cacheDir "icon_$hash.ico"
        if (-not (Test-Path $dest)) { Copy-Item $IconPath $dest -Force }
        Get-ChildItem $cacheDir -Filter "icon_*.ico" -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -ne $dest } |
            Remove-Item -Force -ErrorAction SilentlyContinue
        $IconRef = "$dest,0"
    } catch {
        Write-Host "[WARN] icon cache-busting failed: $($_.Exception.Message)"
        $IconRef = "$IconPath,0"
    }
}

# --- create the shortcut ----------------------------------------------------
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($LinkPath)
$lnk.TargetPath       = $Target
$lnk.WorkingDirectory = $Root
$lnk.Description      = "启动机械臂 Web 控制台（服务 + 页面）"
$lnk.WindowStyle      = 1
if ($IconRef) { $lnk.IconLocation = $IconRef }
$lnk.Save()

Write-Host "Shortcut created:"
Write-Host "  $LinkPath"
Write-Host "  -> $Target"
if ($IconRef) { Write-Host "  icon: $IconRef" }

# tell the shell to re-read this shortcut right away (no Explorer restart)
try {
    Add-Type -Namespace Shell32 -Name Notify -MemberDefinition @'
[DllImport("shell32.dll", CharSet=CharSet.Unicode)]
public static extern void SHChangeNotify(int wEventId, uint uFlags, IntPtr dwItem1, IntPtr dwItem2);
'@
    $p = [Runtime.InteropServices.Marshal]::StringToHGlobalUni($LinkPath)
    [Shell32.Notify]::SHChangeNotify(0x00002000, 0x0005, $p, [IntPtr]::Zero)   # UPDATEITEM
    [Runtime.InteropServices.Marshal]::FreeHGlobal($p)
    [Shell32.Notify]::SHChangeNotify(0x08000000, 0x0000, [IntPtr]::Zero, [IntPtr]::Zero)  # ASSOCCHANGED
} catch {
    Write-Host "[WARN] shell refresh skipped: $($_.Exception.Message)"
}
