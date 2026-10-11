@echo off
rem Local build of dist\POUNCer.exe (dev / quick test).
rem
rem The exe to copy to other PCs should come from the GitHub Actions build
rem (.github\workflows\build-exe.yml): it compiles the PyInstaller bootloader
rem from source, which avoids the Windows Defender false positive
rem "Trojan:Win32/Wacatac.B!ml" that the prebuilt bootloader triggers.
rem A local build uses the prebuilt bootloader unless PyInstaller was
rem installed with:  pip install --no-binary pyinstaller pyinstaller
rem (that needs Visual Studio Build Tools / a C compiler).
setlocal
cd /d "%~dp0"
python -m PyInstaller --noconfirm --clean --distpath dist --workpath build POUNCer.spec
if errorlevel 1 goto :err

rem Ship the editable config alongside the exe.
copy /y config.ini dist\config.ini >nul
echo.
echo Build finished: dist\POUNCer.exe
echo Keep dist\config.ini next to the exe so it can be edited.
goto :eof

:err
echo.
echo PyInstaller build failed.
exit /b 1
