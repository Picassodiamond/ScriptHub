@echo off
:: --- scripthub ---
:: name: Ping a host (batch)
:: description: "Windows batch: ping a host; host and count are positional arguments. Fails when the host does not answer."
:: category: Network
:: platforms: [windows]
:: parameters:
::   - name: host
::     label: Host
::     type: text
::     default: 127.0.0.1
::     required: true
::     pattern: "[A-Za-z0-9.-]+"
::     pass_as: positional
::     position: 1
::   - name: count
::     label: Pings
::     type: number
::     default: 4
::     min: 1
::     max: 20
::     pass_as: positional
::     position: 2
:: --- end scripthub ---
setlocal
if "%~1"=="" (
  1>&2 echo Usage: ping_host.bat HOST [COUNT]
  exit /b 2
)
set "COUNT=%~2"
if "%COUNT%"=="" set "COUNT=4"
ping -n %COUNT% "%~1"
exit /b %ERRORLEVEL%
