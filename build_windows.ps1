$ErrorActionPreference = "Stop"

py -m pip install -r requirements.txt -r requirements-build.txt
py -m PyInstaller --clean --noconfirm AutoSync.spec

Write-Host "Built dist\\AutoSync.exe"
