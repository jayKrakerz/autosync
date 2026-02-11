# ============================================================================
# AutoSync Installer — RiskArena Brokerage Services (Windows)
# Run in PowerShell: irm https://raw.githubusercontent.com/jayKrakerz/autosync/master/install.ps1 | iex
# ============================================================================
$ErrorActionPreference = "Stop"

$Repo = "https://github.com/jayKrakerz/autosync.git"
$InstallDir = "$env:LOCALAPPDATA\AutoSync"
$Port = 8050

# Pre-configured Azure App Registration (shared across the company)
$ClientId = "c4dca575-9641-440e-b2cc-08c4f191698d"
$TenantId = "0716b81d-2dfc-4e22-a250-3c77832c1b0e"

Write-Host ""
Write-Host "  +======================================+" -ForegroundColor Cyan
Write-Host "  |   AutoSync Installer - RiskArena     |" -ForegroundColor Cyan
Write-Host "  +======================================+" -ForegroundColor Cyan
Write-Host ""

# -------------------------------------------------------------------
# 1. Check for Python 3
# -------------------------------------------------------------------
$Python = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $ver = & $cmd --version 2>&1
        if ($ver -match "Python 3") {
            $Python = $cmd
            break
        }
    } catch {}
}
if (-not $Python) {
    Write-Host "ERROR: Python 3 is required." -ForegroundColor Red
    Write-Host "Download from: https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Host "IMPORTANT: Check 'Add Python to PATH' during installation." -ForegroundColor Yellow
    exit 1
}
Write-Host "[1/6] Python found: $Python ($(& $Python --version 2>&1))" -ForegroundColor Green

# -------------------------------------------------------------------
# 2. Check for Git
# -------------------------------------------------------------------
try {
    git --version | Out-Null
} catch {
    Write-Host "ERROR: Git is required." -ForegroundColor Red
    Write-Host "Download from: https://git-scm.com/download/win" -ForegroundColor Yellow
    exit 1
}
Write-Host "[2/6] Git found" -ForegroundColor Green

# -------------------------------------------------------------------
# 3. Clone or update repo
# -------------------------------------------------------------------
if (Test-Path "$InstallDir\.git") {
    Write-Host "[3/6] Updating existing installation..." -ForegroundColor Green
    git -C $InstallDir pull --quiet
} else {
    Write-Host "[3/6] Installing to $InstallDir ..." -ForegroundColor Green
    git clone --quiet $Repo $InstallDir
}

# -------------------------------------------------------------------
# 4. Create virtual environment & install dependencies
# -------------------------------------------------------------------
Write-Host "[4/6] Setting up Python environment..." -ForegroundColor Green
if (-not (Test-Path "$InstallDir\venv")) {
    & $Python -m venv "$InstallDir\venv"
}
& "$InstallDir\venv\Scripts\pip.exe" install --quiet --upgrade pip
& "$InstallDir\venv\Scripts\pip.exe" install --quiet -r "$InstallDir\requirements.txt"

# -------------------------------------------------------------------
# 5. Pre-configure Azure App
# -------------------------------------------------------------------
Write-Host "[5/6] Configuring app..." -ForegroundColor Green
$ConfigFile = "$InstallDir\user_config.json"
if (-not (Test-Path $ConfigFile)) {
    $SyncFolder = "$env:USERPROFILE\OneDrive Sync"
    $config = @{
        client_id = $ClientId
        tenant_id = $TenantId
        local_folder = $SyncFolder
        poll_interval = 300
    } | ConvertTo-Json
    $config | Out-File -FilePath $ConfigFile -Encoding utf8
} else {
    Write-Host "       Existing config found, keeping it." -ForegroundColor Gray
}

# -------------------------------------------------------------------
# 6. Create Startup shortcut (auto-start on login)
# -------------------------------------------------------------------
Write-Host "[6/6] Setting up auto-start..." -ForegroundColor Green
$StartupDir = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup"
$VbsPath = "$StartupDir\AutoSync.vbs"
$PythonW = "$InstallDir\venv\Scripts\pythonw.exe"
$AppPy = "$InstallDir\app.py"

$vbs = @"
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "$InstallDir"
WshShell.Run """$PythonW"" ""$AppPy"" --no-gui", 0, False
"@
$vbs | Out-File -FilePath $VbsPath -Encoding ascii

# Start the headless server now
Write-Host ""
Write-Host "  Starting AutoSync..." -ForegroundColor Cyan
Start-Process -FilePath "$PythonW" -ArgumentList "$AppPy --no-gui" -WorkingDirectory $InstallDir -WindowStyle Hidden

# Wait for server to be ready
for ($i = 0; $i -lt 15; $i++) {
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:$Port/api/health" -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -eq 200) { break }
    } catch {}
    Start-Sleep -Seconds 1
}

Write-Host ""
Write-Host "  AutoSync installed successfully!" -ForegroundColor Green
Write-Host ""
Write-Host "  Dashboard:   http://localhost:$Port" -ForegroundColor White
Write-Host "  Sync folder: $env:USERPROFILE\OneDrive Sync" -ForegroundColor White
Write-Host "  Install dir: $InstallDir" -ForegroundColor White
Write-Host ""
Write-Host "  Next steps:" -ForegroundColor Yellow
Write-Host "    1. Your browser will open the dashboard"
Write-Host "    2. Click 'Sign in with Microsoft' and log in with your work account"
Write-Host "    3. Paste your OneDrive shared folder link in Settings"
Write-Host "    4. Click Start Sync"
Write-Host ""
Write-Host "  AutoSync will start automatically on login." -ForegroundColor Green
Write-Host ""

# Launch native app window (server is already running headless)
$PythonExe = "$InstallDir\venv\Scripts\python.exe"
Start-Process -FilePath "$PythonExe" -ArgumentList "$AppPy" -WorkingDirectory $InstallDir
