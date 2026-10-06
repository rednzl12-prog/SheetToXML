# Release build

```bat
python -B packaging/build.py            :: full build (~2 min)
python -B packaging/build.py --installer-only   :: reuse dist/SheetXML, recompile the installer only
```

Needs: Python with `requirements.txt` installed (PyInstaller is pip-installed if missing), the C# compiler that
ships with Windows (`%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe`), and the Audiveris installer at
`packaging/vendor/Audiveris-5.11.0-windows-x86_64.msi` (85,062,430 bytes, from
https://github.com/Audiveris/audiveris/releases/tag/5.11.0; `vendor/` is gitignored).

Output (`dist/`, gitignored):

- `dist/SheetXML/`: the portable program folder: `SheetXML.exe` (windowed: starts the local server and opens the
  browser), `SheetXML-cli.exe` (console: `SheetXML-cli score.pdf`, drag and drop), `uninstall.exe`, `_internal/`.
- `dist/SheetXML-Setup-<VERSION>.exe`: the installer (program folder + Audiveris MSI + license text embedded).

The version is `VERSION` in `build.py`. The build smoke-tests the frozen app (CLI on `resources/Pachelbel_Canon.pdf`,
server answers `/api/config`) with `SHEETXML_DATA_DIR` in a temp folder.

## Installer

`packaging/installer.cs`, one source for the installer and `uninstall.exe`. Per user, no admin: installs to
`%LOCALAPPDATA%\Programs\SheetXML`, Start menu folder (SheetXML, SheetXML Command Line, Uninstall), optional desktop
shortcut, entry in Windows Settings > Apps (`HKCU\...\Uninstall\SheetXML`). The Audiveris option runs
`msiexec /i ... /passive /norestart` elevated (one UAC prompt); it is unchecked when Audiveris is already installed.
Reinstalling stops a running SheetXML from that folder and replaces the files (upgrade). User data (keys,
transcriptions, logs) lives in `%LOCALAPPDATA%\SheetXML`; the uninstaller asks before deleting it (default keep) and
never removes Audiveris. It deletes only the files listed in `files.txt`.

Silent: `SheetXML-Setup-<v>.exe /S [/D=<dir>] [/NOAUDIVERIS] [/NODESKTOP]`, `uninstall.exe /uninstall /S`.
Log: `%TEMP%\SheetXML-setup.log`. The exes are unsigned, so SmartScreen shows "Windows protected your PC"
(More info > Run anyway) until they are code-signed.
