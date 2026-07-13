@echo off
rem AeroGuard one-click launcher (Windows). Double-click:
rem installs Docker Desktop if missing, starts it if needed, brings the
rem stack up, then opens the HMI in a chromeless app window with the
rem API key pre-loaded.
setlocal
cd /d "%~dp0"

rem Docker Desktop bundles its CLI here; covers a just-installed copy
rem whose PATH entry is not visible to this session yet.
set "PATH=%PATH%;%ProgramFiles%\Docker\Docker\resources\bin"

where docker >nul 2>nul
if not errorlevel 1 goto docker_present
if exist "%ProgramFiles%\Docker\Docker\Docker Desktop.exe" goto docker_present

echo Docker Desktop not found - installing it now (one time, large download)...
winget install -e --id Docker.DockerDesktop --accept-source-agreements --accept-package-agreements
if not errorlevel 1 goto docker_present
echo winget unavailable - downloading the installer directly...
curl -L -o "%TEMP%\DockerDesktopInstaller.exe" "https://desktop.docker.com/win/main/amd64/Docker%%20Desktop%%20Installer.exe"
if errorlevel 1 (
  echo Could not download Docker Desktop. Install it from docker.com, then run this again.
  pause
  exit /b 1
)
"%TEMP%\DockerDesktopInstaller.exe" install --quiet --accept-license
if errorlevel 1 (
  echo Docker Desktop installation failed. Install it from docker.com, then run this again.
  pause
  exit /b 1
)
del "%TEMP%\DockerDesktopInstaller.exe" >nul 2>nul

:docker_present
docker info >nul 2>nul
if not errorlevel 1 goto docker_ready

rem Docker Desktop runs on the WSL 2 backend; a missing or outdated WSL
rem shows up as "There was a problem with WSL" in Docker Desktop.
wsl --status >nul 2>nul
if errorlevel 1 (
  echo WSL 2 is not set up - installing it now. Approve the admin prompt if shown.
  wsl --install --no-distribution
  echo If Windows asks to reboot, reboot and run this launcher again.
) else (
  wsl --update >nul 2>nul
)

echo Starting Docker Desktop...
echo (first run only: accept the service agreement in the Docker window -
echo  this launcher continues automatically once Docker is ready)
start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
for /l %%i in (1,1,150) do (
  timeout /t 2 /nobreak >nul
  docker info >nul 2>nul && goto docker_ready
)
echo Docker did not become ready. If Docker Desktop was just installed,
echo sign out and back in (or reboot), then run this again.
echo If Docker Desktop shows "There was a problem with WSL", open
echo PowerShell as Administrator, run:  wsl --update
echo then reboot and run this launcher again.
pause
exit /b 1

:docker_ready
if exist .env goto env_ready
for /f "usebackq delims=" %%k in (`powershell -NoProfile -Command "$b = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); [Convert]::ToBase64String($b)"`) do set "KEY=%%k"
echo AEROGUARD_API_KEY=%KEY%> .env

:env_ready
rem Port 8000 already serving AeroGuard from a different copy of the repo?
curl -sf http://127.0.0.1:8000/healthz >nul 2>nul
if not errorlevel 1 (
  docker compose ps -q 2>nul | findstr . >nul || (
    echo An AeroGuard instance from another folder is already running on
    echo port 8000. Use it at http://127.0.0.1:8000, or stop it first with
    echo "docker compose down" in the folder it was started from.
    pause
    exit /b 1
  )
)

echo Starting AeroGuard (first run builds the image - several minutes)...
docker compose up -d
if errorlevel 1 (
  echo docker compose failed. If the error mentions port 8000 "already
  echo allocated", another AeroGuard copy is running - stop it with
  echo "docker compose down" in that folder. Otherwise inspect with:
  echo   docker compose logs
  pause
  exit /b 1
)

echo Waiting for the backend to become healthy...
for /l %%i in (1,1,90) do (
  curl -sf http://127.0.0.1:8000/healthz >nul 2>nul && goto backend_ready
  timeout /t 1 /nobreak >nul
)
echo Backend did not become healthy. Inspect with: docker compose logs
pause
exit /b 1

:backend_ready
for /f "usebackq tokens=1,* delims==" %%a in (".env") do if "%%a"=="AEROGUARD_API_KEY" set "KEY=%%b"
set "URL=http://127.0.0.1:8000/#key=%KEY%"

start msedge --app="%URL%" 2>nul || start chrome --app="%URL%" 2>nul || start "" "%URL%"

echo AeroGuard is running. Closing the window leaves the server up;
echo stop it with: docker compose down
endlocal
