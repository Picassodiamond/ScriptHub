@echo off
:: --- scripthub ---
:: {
::   "name": "Ping Host (batch)",
::   "description": "Windows .bat example: ping a host using positional arguments. Fails when the host cannot be reached.",
::   "category": "System",
::   "parameters": [
::     {"name": "host", "label": "Host", "type": "text", "default": "127.0.0.1", "required": true, "pattern": "[A-Za-z0-9.-]+", "pass_as": "positional", "position": 1, "help": "Host name or IP address (letters, digits, dots, hyphens)"},
::     {"name": "count", "label": "Number of pings", "type": "number", "default": 4, "required": true, "min": 1, "max": 20, "pass_as": "positional", "position": 2}
::   ]
:: }
:: --- end scripthub ---
setlocal
rem Ping a host a number of times.
rem
rem Usage: ping_host.bat HOST [COUNT]
rem
rem Both values are POSITIONAL arguments (see the metadata block above). In a batch
rem file they are read as %1 and %2; %~1 removes surrounding quotes.
rem Windows only.

if "%~1"=="" (
  1>&2 echo Usage: ping_host.bat HOST [COUNT]
  exit /b 2
)

set "COUNT=%~2"
if "%COUNT%"=="" set "COUNT=4"

echo Pinging %~1 %COUNT% time(s)...
ping -n %COUNT% "%~1"
exit /b %ERRORLEVEL%
