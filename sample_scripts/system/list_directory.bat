@echo off
:: --- scripthub ---
:: {
::   "name": "List a folder (batch)",
::   "description": "Windows batch: list a folder; the folder is a positional argument, the style an environment variable.",
::   "category": "System",
::   "platforms": ["windows"],
::   "parameters": [
::     {"name": "folder", "label": "Folder", "type": "directory", "default": ".", "required": true, "pass_as": "positional", "position": 1},
::     {"name": "mode", "label": "Style", "type": "select", "choices": ["brief", "full"], "default": "brief", "pass_as": "env", "env_name": "LIST_MODE"}
::   ]
:: }
:: --- end scripthub ---
setlocal
if not exist "%~1\" (
  1>&2 echo Not a folder: %~1
  exit /b 1
)
if /i "%LIST_MODE%"=="full" (
  dir "%~1"
) else (
  dir /b "%~1"
)
exit /b %ERRORLEVEL%
