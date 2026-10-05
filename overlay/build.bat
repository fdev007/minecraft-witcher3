@echo off
rem Builds mcw_overlay.exe with MSVC (VS 2022 or Build Tools, "Desktop development with C++") + Windows SDK
rem (C++/WinRT headers ship with it). The newest Visual Studio with the x64 C++ tools is found via vswhere.
setlocal
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist "%VSWHERE%" (
  echo vswhere.exe not found: install Visual Studio 2022 or Build Tools with "Desktop development with C++"
  exit /b 1
)
set "VSDIR="
for /f "usebackq delims=" %%i in (`call "%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSDIR=%%i"
if not defined VSDIR (
  echo No Visual Studio with the x64 C++ tools found
  exit /b 1
)
call "%VSDIR%\VC\Auxiliary\Build\vcvars64.bat" >nul 2>&1 || exit /b 1
cd /d "%~dp0"
cl /nologo /std:c++20 /EHsc /O2 /W3 /DUNICODE /D_UNICODE /DNOMINMAX mcw_overlay.cpp /Fe:mcw_overlay.exe /link /SUBSYSTEM:CONSOLE || exit /b 1
del /q mcw_overlay.obj 2>nul
exit /b 0
