@echo off
rem AeroGuard one-click launcher (Windows). Double-click:
rem starts Docker if needed, brings the stack up, then opens the HMI
rem in a chromeless app window with the API key pre-loaded.
setlocal
cd /d "%~dp0"

where docker >nul 2>nul
if errorlevel 1 (
  echo Docker Desktop is not installed. Get it from docker.com, then run this again.
  pause
  exit /b 1
)

docker info >nul 2>nul
if not errorlevel 1 goto docker_ready
echo Starting Docker Desktop...
start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
for /l %%i in (1,1,60) do (
  timeout /t 2 /nobreak >nul
  docker info >nul 2>nul && goto docker_ready
)
echo Docker did not become ready. Start Docker Desktop manually, then run this again.
pause
exit /b 1

:docker_ready
if exist .env goto env_ready
for /f "usebackq delims=" %%k in (`powershell -NoProfile -Command "$b = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); [Convert]::ToBase64String($b)"`) do set "KEY=%%k"
echo AEROGUARD_API_KEY=%KEY%> .env

:env_ready
echo Starting AeroGuard (first run builds the image - several minutes)...
docker compose up -d
if errorlevel 1 (
  echo docker compose failed. Inspect with: docker compose logs
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
