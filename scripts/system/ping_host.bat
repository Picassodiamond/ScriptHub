@echo off
setlocal
rem Ping a host a number of times.
rem
rem Usage: ping_host.bat HOST [COUNT]
rem
rem Both values are POSITIONAL arguments (see ping_host.json). In a batch
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
