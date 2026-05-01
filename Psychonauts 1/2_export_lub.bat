@echo off
chcp 65001 > nul

echo ========================================
echo   Psychonauts LUB Export Tool
echo ========================================
echo.

if exist "exported_lub" (
    echo Output directory 'exported_lub' already exists.
    echo Exiting.
    pause
    exit /b
) else (
    mkdir "exported_lub"
)


echo Exporting LUB files...
python text_tool.py export --all WorkResource\Localization\English exported_lub --character-csv speaker.csv

echo.
echo All Done!
pause
