# ============================================================================
# AutoSync Uninstaller — RiskArena Brokerage Services (Windows)
# ============================================================================
$ErrorActionPreference = "Stop"

$InstallDir = "$env:LOCALAPPDATA\AutoSync"
$VbsPath = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\AutoSync.vbs"

Write-Host ""
Write-Host "  Uninstalling AutoSync..." -ForegroundColor Cyan
Write-Host ""

# Stop running AutoSync processes
Get-Process -Name "pythonw" -ErrorAction SilentlyContinue | ForEach-Object {
    if ($_.Path -like "*AutoSync*") {
        Stop-Process -Id $_.Id -Force
        Write-Host "  Stopped AutoSync process" -ForegroundColor Green
    }
}

# Remove Startup shortcut
if (Test-Path $VbsPath) {
    Remove-Item -Path $VbsPath -Force
    Write-Host "  Startup shortcut removed" -ForegroundColor Green
}

# Remove installation
if (Test-Path $InstallDir) {
    Remove-Item -Path $InstallDir -Recurse -Force
    Write-Host "  Installation removed ($InstallDir)" -ForegroundColor Green
}

Write-Host ""
Write-Host "  AutoSync uninstalled." -ForegroundColor Green
Write-Host "  Note: Your synced files in $env:USERPROFILE\OneDrive Sync were NOT deleted." -ForegroundColor Yellow
Write-Host ""
