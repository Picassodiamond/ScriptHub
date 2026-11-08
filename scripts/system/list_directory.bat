@echo off
:: --- scripthub ---
:: {
::   "name": "List Directory (batch)",
::   "description": "Windows .bat example: one positional argument (directory) and one environment variable (LIST_MODE).",
::   "category": "System",
::   "parameters": [
::     {"name": "directory", "label": "Directory", "type": "directory", "default": "C:\\Windows", "required": true, "pass_as": "positional", "position": 1},
::     {"name": "mode", "label": "Listing mode", "type": "select", "default": "brief", "required": true, "choices": [{"value": "brief", "label": "Names only"}, {"value": "full", "label": "Full details"}], "pass_as": "env", "env_name": "LIST_MODE"}
::   ]
:: }
:: --- end scripthub ---
setlocal
rem List a directory, either names only or a full listing.
rem
rem Usage: set LIST_MODE=brief|full & list_directory.bat DIRECTORY
rem
rem   DIRECTORY - POSITIONAL argument (%1)
rem   LIST_MODE - ENVIRONMENT VARIABLE (see the metadata block above)
rem Windows only.

if "%~1"=="" (
  1>&2 echo Usage: list_directory.bat DIRECTORY
  exit /b 2
)
if not exist "%~1\" (
  1>&2 echo Error: not a directory: %~1
  exit /b 1
)

if "%LIST_MODE%"=="" set "LIST_MODE=brief"
echo Listing %~1 (mode: %LIST_MODE%)
echo ----------------------------------------

if /i "%LIST_MODE%"=="full" (
  dir /a "%~1"
) else (
  dir /b "%~1"
)
exit /b %ERRORLEVEL%
