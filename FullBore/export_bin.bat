@echo off
setlocal
set "ROOT=%~dp0"
set "EXITCODE=0"

if /I "%~1"=="/?" goto :usage
if /I "%~1"=="-h" goto :usage
if /I "%~1"=="--help" goto :usage

pushd "%ROOT%" >nul

set "SRC=%~1"
if not defined SRC set "SRC=text\en.bin"

set "DST=%~2"
if not defined DST set "DST=en.json"

set "MODE=%~3"
if not defined MODE set "MODE=clean"

if /I not "%MODE%"=="clean" if /I not "%MODE%"=="exact" (
  echo ERROR: mode must be clean or exact.
  set "EXITCODE=1"
  goto :end
)

if not exist "%SRC%" (
  echo ERROR: source BIN not found: %SRC%
  set "EXITCODE=1"
  goto :end
)

if not exist "%ROOT%fullbore_text.py" (
  echo ERROR: fullbore_text.py not found next to this BAT.
  set "EXITCODE=1"
  goto :end
)

python "%ROOT%fullbore_text.py" export --mode %MODE% "%SRC%" "%DST%"
set "EXITCODE=%ERRORLEVEL%"
goto :end

:usage
echo Usage:
echo   export_bin.bat [src_bin] [dst_json] [clean^|exact]
echo.
echo Defaults:
echo   src_bin  = text\en.bin
echo   dst_json = en.json
echo   mode     = clean
goto :end

:end
popd >nul 2>nul
pause
exit /b %EXITCODE%
