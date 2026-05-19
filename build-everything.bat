@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

REM ==================================================================
REM EKTRO one-click full build (D-015 ASCII-only)
REM   1. find VS 2022 BuildTools + load vcvars64
REM   2. build EKTRO C++ lib + tests (~3 min first time)
REM   3. build Boost (if missing, ~30-60 min first time)
REM   4. build librime + weasel (~10-20 min)
REM Not automated (user must do):
REM   - install weasel-setup.exe (admin)
REM   - copy default.custom.yaml
REM   - switch IME + Notepad verify
REM ==================================================================

cd /d "%~dp0"
set "EKTRO_ROOT=%CD%"
set "WEASEL_ROOT=%EKTRO_ROOT%\upstream\weasel-master"

if exist "E:\bx\bootstrap.bat" (
    set "BOOST_ROOT=E:\bx"
) else (
    set "BOOST_ROOT=%WEASEL_ROOT%\deps\boost_1_84_0"
)

REM D-015 sandbox safeguard
set "NoDefaultCurrentDirectoryInExePath="

echo ================================================================
echo  EKTRO build (Boost ~30-60 min + Weasel ~10-20 min)
echo ================================================================
echo.

REM ============ 1. VS Developer Environment ============

echo [1/5] Loading VS 2022 BuildTools vcvars64 ...
set "VSWHERE=C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"
set "VS_INSTALL="
if exist "%VSWHERE%" (
    for /f "usebackq tokens=*" %%i in (`"%VSWHERE%" -latest -products * -property installationPath 2^>nul`) do (
        set "VS_INSTALL=%%i"
    )
)
if not defined VS_INSTALL set "VS_INSTALL=C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools"
if not exist "!VS_INSTALL!\VC\Auxiliary\Build\vcvars64.bat" set "VS_INSTALL=C:\Program Files\Microsoft Visual Studio\2022\BuildTools"
if not exist "!VS_INSTALL!\VC\Auxiliary\Build\vcvars64.bat" set "VS_INSTALL=C:\Program Files\Microsoft Visual Studio\2022\Community"
if not exist "!VS_INSTALL!\VC\Auxiliary\Build\vcvars64.bat" (
    echo ERROR: vcvars64.bat not found. Please install VS 2022 BuildTools.
    exit /b 1
)
call "!VS_INSTALL!\VC\Auxiliary\Build\vcvars64.bat" >nul
echo     [OK] MSVC env loaded
echo     [OK] VS_INSTALL=!VS_INSTALL!

REM ============ 2. EKTRO C++ lib ============

echo.
echo [2/5] Building EKTRO C++ library ...
cd /d "%EKTRO_ROOT%\src-cpp"
set "NINJA_DIR=C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja"
set "PATH=%NINJA_DIR%;%PATH%"
if not exist build (
    cmake -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER=cl -DCMAKE_CXX_COMPILER=cl
    if errorlevel 1 (
        echo ERROR: cmake configure failed
        exit /b 1
    )
)
cmake --build build --target ektro ektro_tests
if errorlevel 1 (
    echo ERROR: ektro build failed
    exit /b 1
)
echo     [OK] ektro.lib + ektro_tests.exe built

echo.
echo [2.5/5] Running EKTRO unit tests ...
"build\ektro_tests.exe" --gtest_brief=1
if errorlevel 1 (
    echo ERROR: ektro tests failed
    exit /b 1
)
echo     [OK] All 22 GoogleTest passed

REM ============ 3. Boost ============

echo.
echo [3/5] Checking Boost at %BOOST_ROOT% ...
if exist "%BOOST_ROOT%\stage\lib\libboost_wserialization-vc143-mt-s-x64-1_84.lib" goto boost_done
if exist "%BOOST_ROOT%\stage\lib\libboost_wserialization-vc143-mt-s-1_84.lib" goto boost_done
goto boost_build
:boost_done
echo     [OK] Boost already built
goto boost_after
:boost_build
if not exist "%BOOST_ROOT%\bootstrap.bat" (
    echo ERROR: Boost source missing at %BOOST_ROOT%
    exit /b 1
)
cd /d "%BOOST_ROOT%"
if not exist b2.exe (
    echo     Bootstrapping b2 ...
    call bootstrap.bat vc143
    if errorlevel 1 (
        echo ERROR: bootstrap failed
        exit /b 1
    )
)
if not exist project-config.jam (
    echo import option ; > project-config.jam
    echo using msvc : 14.3 : "C:/Program Files (x86)/Microsoft Visual Studio/2022/BuildTools/VC/Tools/MSVC/14.44.35207/bin/Hostx64/x64/cl.exe" : ^<setup^>"C:/Program Files (x86)/Microsoft Visual Studio/2022/BuildTools/VC/Auxiliary/Build/vcvarsall.bat" ; >> project-config.jam
    echo option.set keep-going : false ; >> project-config.jam
)
echo     Building Boost (30-60 min first time) ...
b2 --with-locale --with-regex --with-system --with-filesystem --with-chrono --with-thread --with-date_time --with-serialization toolset=msvc-14.3 link=static runtime-link=static threading=multi variant=release address-model=64 -j4
if errorlevel 1 (
    echo ERROR: boost b2 build failed
    exit /b 1
)
echo     [OK] Boost built
:boost_after

REM ============ 4. Weasel ============

echo.
echo [4/5] Building Weasel (with EKTRO patches) ...
REM EKTRO D-015: force Ninja for librime deps (VS generator detection broken post-ATL)
set "CMAKE_GENERATOR=Ninja"
set "NINJA_DIR=C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja"
set "PATH=%NINJA_DIR%;%PATH%"
cd /d "%WEASEL_ROOT%"
call build.bat
if errorlevel 1 (
    echo ERROR: weasel build failed
    exit /b 1
)
echo     [OK] Weasel built

REM ============ 5. Done ============

echo.
echo ================================================================
echo  [OK] EKTRO + Weasel build SUCCESS
echo ================================================================
echo.
echo Next manual steps (need admin):
echo   1. Run as admin: %WEASEL_ROOT%\output\weasel-setup.exe
echo   2. Copy: %EKTRO_ROOT%\config\default.custom.yaml to %%APPDATA%%\Rime\
echo   3. Right-click Weasel tray icon -> Redeploy
echo   4. Win+Space switch to Rime, Notepad type nihaoshijie
echo      [OK] "Ni Hao Shi Jie" inline (no candidate popup)
echo      [OK] Space to commit
echo      [OK] Hold Ctrl >=500ms shows emergency candidates
echo.

endlocal
