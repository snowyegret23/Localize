@echo off
setlocal
set "ROOT=%~dp0"
set "EXITCODE=0"

if /I "%~1"=="/?" goto :usage
if /I "%~1"=="-h" goto :usage
if /I "%~1"=="--help" goto :usage

pushd "%ROOT%" >nul

set "FONT=%~1"
if not defined FONT set "FONT=Mulmaru.ttf"

set "EXE=%~2"
if not defined EXE set "EXE=FullBore.exe"

set "OUT=%~3"
if not defined OUT (
  set "OUT=%ROOT%_fontpatch_tmp.exe"
  set "INPLACE=1"
) else (
  set "INPLACE=0"
)

if not exist "%FONT%" (
  echo ERROR: font file not found: %FONT%
  set "EXITCODE=1"
  goto :end
)

if not exist "%EXE%" (
  echo ERROR: EXE not found: %EXE%
  set "EXITCODE=1"
  goto :end
)

if not exist "%ROOT%fullbore_dynafont_patch.py" (
  echo ERROR: fullbore_dynafont_patch.py not found next to this BAT.
  set "EXITCODE=1"
  goto :end
)

python "%ROOT%fullbore_dynafont_patch.py" --exe "%EXE%" --font "%FONT%" --out "%OUT%"

if errorlevel 1 (
  if exist "%ROOT%_fontpatch_tmp.exe" del /q "%ROOT%_fontpatch_tmp.exe" >nul 2>nul
  set "EXITCODE=1"
  goto :end
)

if "%INPLACE%"=="1" (
  if not exist "%EXE%.bak" copy /y "%EXE%" "%EXE%.bak" >nul
  move /y "%OUT%" "%EXE%" >nul
  if errorlevel 1 (
    echo ERROR: failed to replace %EXE%
    if exist "%OUT%" del /q "%OUT%" >nul 2>nul
    set "EXITCODE=1"
    goto :end
  )
  echo Patched in place: %EXE%
) else (
  echo Wrote: %OUT%
)

goto :end

:usage
echo Usage:
echo   fontpatch.bat [font_file] [target_exe] [out_exe]
echo.
echo Defaults:
echo   font_file  = Mulmaru.ttf
echo   target_exe = FullBore.exe
echo   out_exe    = overwrite target_exe in place ^(creates .bak once^)
goto :end

:end
popd >nul 2>nul
pause
exit /b %EXITCODE%
