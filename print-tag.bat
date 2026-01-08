@echo off
REM Print a ZPL file to Zebra ZT421 printer
if "%1"=="" (
    echo Usage: print-tag.bat ^<zpl-file^>
    echo Example: print-tag.bat zpl_output\apriltag_tagStandard41h12_00650.zpl
    exit /b 1
)
echo Printing %1 to ZDesigner ZT421-300dpi ZPL...
powershell -Command "Get-Content '%1' -Raw | Out-Printer -Name 'ZDesigner ZT421-300dpi ZPL'"
echo Done.
