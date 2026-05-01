@echo off
setlocal
set "ROOT=%~dp0"
set "EXITCODE=0"

if /I "%~1"=="/?" goto :usage
if /I "%~1"=="-h" goto :usage
if /I "%~1"=="--help" goto :usage

pushd "%ROOT%" >nul

set "SRC=%~1"
if not defined SRC set "SRC=en.json"

set "DST=%~2"
if not defined DST set "DST=text\en.bin"

set "COPY_TO_JP=%~3"
set "TMP=%ROOT%_import_tmp.bin"

if not exist "%SRC%" (
  echo ERROR: source JSON not found: %SRC%
  set "EXITCODE=1"
  goto :end
)

for %%I in ("%DST%") do (
  if not exist "%%~dpI" mkdir "%%~dpI" >nul 2>nul
)

if not exist "%ROOT%fullbore_text.py" (
  echo ERROR: fullbore_text.py not found next to this BAT.
  set "EXITCODE=1"
  goto :end
)

python "%ROOT%fullbore_text.py" import "%SRC%" "%TMP%"

if errorlevel 1 (
  if exist "%TMP%" del /q "%TMP%" >nul 2>nul
  set "EXITCODE=1"
  goto :end
)

if exist "%DST%" (
  if not exist "%DST%.bak" copy /y "%DST%" "%DST%.bak" >nul
)

move /y "%TMP%" "%DST%" >nul
if errorlevel 1 (
  echo ERROR: failed to write %DST%
  if exist "%TMP%" del /q "%TMP%" >nul 2>nul
  set "EXITCODE=1"
  goto :end
)

if /I "%COPY_TO_JP%"=="--also-jp" (
  if not exist "text" mkdir "text" >nul 2>nul
  if exist "text\jp.bin" (
    if not exist "text\jp.bin.bak" copy /y "text\jp.bin" "text\jp.bin.bak" >nul
  )
  copy /y "%DST%" "text\jp.bin" >nul
  if errorlevel 1 (
    echo ERROR: failed to copy %DST% to text\jp.bin
    set "EXITCODE=1"
    goto :end
  )
  echo Updated: %DST% and text\jp.bin
) else (
  echo Updated: %DST%
)

goto :end

:usage
echo Usage:
echo   import_bin.bat [src_json] [dst_bin] [--also-jp]
echo.
echo Defaults:
echo   src_json = en.json
echo   dst_bin  = text\en.bin
echo.
echo Optional:
echo   --also-jp  also copies the rebuilt BIN to text\jp.bin
goto :end

:end
popd >nul 2>nul
pause
exit /b %EXITCODE%
