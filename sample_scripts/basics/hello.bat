@echo off
:: --- scripthub ---
:: {
::   "name": "Hello (batch)",
::   "description": "Windows batch version: the name is the first positional argument (%1).",
::   "category": "Basics",
::   "platforms": ["windows"],
::   "parameters": [
::     {"name": "name", "label": "Name", "type": "text", "default": "World", "required": true, "pattern": "[A-Za-z0-9 ._-]+", "pass_as": "positional", "position": 1}
::   ]
:: }
:: --- end scripthub ---
setlocal
set "NAME=%~1"
if "%NAME%"=="" set "NAME=World"
echo Hello, %NAME%!
echo Running as %USERNAME% on %COMPUTERNAME%
exit /b 0
