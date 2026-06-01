@echo off
setlocal
cd /d "%~dp0"
wscript.exe "%~dp0start_geo_getter.vbs"
endlocal
