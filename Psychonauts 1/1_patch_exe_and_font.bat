@echo off
chcp 65001 > nul

echo Refreshing CharList from current translations...
build_used_charlist.exe
echo.

if exist korean_font_output rd /s /q korean_font_output
if not exist korean_font_output mkdir korean_font_output

echo Generating Font Resources...
create_korean_font.exe generate Galmuri9.ttf CharList_3864.txt korean_font_output/Arial_lin.json korean_font_output/Arial_lin.png 24
create_korean_font.exe generate Cafe24PROSlimMax.ttf CharList_3864.txt korean_font_output/bagel_lin.json korean_font_output/bagel_lin.png 24
create_korean_font.exe generate NanumGothic.ttf CharList_3864.txt korean_font_output/Tahoma_lin.json korean_font_output/Tahoma_lin.png 24
create_korean_font.exe generate Cafe24Ssukssuk-v2.0.ttf CharList_3864.txt korean_font_output/RazNotebook_lin.json korean_font_output/RazNotebook_lin.png 24

echo.
echo Packing DFF Files...
create_korean_font.exe pack korean_font_output/Arial_lin.json korean_font_output/Arial_lin.png WorkResource/Fonts/Arial_lin.dff
create_korean_font.exe pack korean_font_output/Arial_lin.json korean_font_output/Arial_lin.png WorkResource/Fonts/Arial_swz.dff
create_korean_font.exe pack korean_font_output/bagel_lin.json korean_font_output/bagel_lin.png WorkResource/Fonts/bagel_lin.dff
create_korean_font.exe pack korean_font_output/bagel_lin.json korean_font_output/bagel_lin.png WorkResource/Fonts/bagel_swz.dff
create_korean_font.exe pack korean_font_output/Tahoma_lin.json korean_font_output/Tahoma_lin.png WorkResource/Fonts/Tahoma_lin.dff
create_korean_font.exe pack korean_font_output/Tahoma_lin.json korean_font_output/Tahoma_lin.png WorkResource/Fonts/Tahoma_swz.dff
create_korean_font.exe pack korean_font_output/RazNotebook_lin.json korean_font_output/RazNotebook_lin.png WorkResource/Fonts/RazNotebook_lin.dff
create_korean_font.exe pack korean_font_output/RazNotebook_lin.json korean_font_output/RazNotebook_lin.png WorkResource/Fonts/RazNotebook_swz.dff

echo.
echo Applying Patch to EXE...
apply_patch.exe --with-korean korean_font_output/Arial_lin.json

echo.
echo All Done!
pause
