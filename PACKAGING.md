# AutoSync Packaging

## Runtime Data

Packaged builds store user-writable files outside the app bundle/exe:

- macOS: `~/Library/Application Support/AutoSync`
- Windows: `%APPDATA%\AutoSync`
- Linux: `$XDG_DATA_HOME/autosync` or `~/.local/share/autosync`

Override with `AUTOSYNC_DATA_DIR` when testing.

## macOS Build

Run on macOS:

```bash
chmod +x build_macos.sh
./build_macos.sh
```

Output:

```text
dist/AutoSync.app
```

## Windows Build

Run on Windows PowerShell:

```powershell
.\build_windows.ps1
```

Output:

```text
dist\AutoSync.exe
```

Optional installer using Inno Setup:

```powershell
iscc installer_windows.iss
```

Output:

```text
installer\AutoSyncSetup.exe
```

## Notes

- Build Windows installers on Windows. PyInstaller does not cross-compile Windows executables from macOS.
- Unsigned builds may trigger macOS Gatekeeper or Windows SmartScreen warnings.
- First launch opens the local dashboard at `http://localhost:8050`.
