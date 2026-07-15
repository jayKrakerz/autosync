#!/usr/bin/env bash
set -euo pipefail

python3 -m pip install -r requirements.txt -r requirements-build.txt
python3 -m PyInstaller --clean --noconfirm AutoSync.spec

echo "Built dist/AutoSync.app"
