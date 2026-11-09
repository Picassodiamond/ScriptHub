ScriptHub - portable Windows build
==================================

Start:   double-click ScriptHub.exe (the app opens in its own window, with a tray icon).
Needs:   Windows 10 or 11 (64-bit) with Microsoft Edge (or Chrome). Nothing to install.

First start: Windows may show "Windows protected your PC" because ScriptHub.exe is not
code-signed. Click "More info", then "Run anyway".

What is in this folder
  ScriptHub.exe   starts the app (it runs python\pythonw.exe desktop.pyw)
  python\         ScriptHub's own Python {python_version}; your .py scripts run on it
  runtime\        your settings, history, presets and sequences (portable mode: kept here,
                  nothing is written to AppData)
  sample_scripts\ example scripts to open with File > Open folder
  the rest        ScriptHub {version} itself

Your scripts
  .py   run on the bundled Python (standard library only). For scripts that need extra
        packages, set Settings > Interpreters > .py to your own Python (full path to python.exe).
  .bat  run with cmd as usual.
  .sh   need Bash (Git Bash or WSL) on PATH, or run them on a Linux host over SSH.

Update: download the new zip, unzip it next to this one and copy the runtime\ folder across.
Move:   the whole folder can be moved or copied (for example to a USB stick).

Documentation, source and issues: https://github.com/Picassodiamond/ScriptHub
