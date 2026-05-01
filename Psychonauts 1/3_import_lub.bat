@echo off
chcp 65001 > nul

echo ========================================
echo   Psychonauts LUB Import Tool
echo ========================================
echo.

echo Importing LUB files...
python text_tool.py import --all WorkResource\Localization\English exported_lub WorkResource\Localization\English

echo.
echo All Done!
pause
