# Reachy Mini - MuJoCo 3D Simulation & Conversation App Launcher
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$Host.UI.RawUI.WindowTitle = "Reachy Mini - DeskMate Launcher"
Set-Location $PSScriptRoot

Clear-Host
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "  Reachy Mini - MuJoCo 3D Simulation & Web UI Launcher" -ForegroundColor Cyan
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "[0/3] Cleaning up previous zombie processes on ports 8000 & 7860..." -ForegroundColor Gray
Get-Process -Name reachy-mini-daemon -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Get-NetTCPConnection -LocalPort 8000, 7860 -ErrorAction SilentlyContinue | ForEach-Object {
    Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 1

Write-Host "[1/3] Launching MuJoCo 3D Simulation Daemon in a new window..." -ForegroundColor Green
Start-Process powershell.exe -ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-Command", "`$Host.UI.RawUI.WindowTitle = 'Reachy Mini - MuJoCo 3D Daemon'; uv run reachy-mini-daemon --sim" -WorkingDirectory $PSScriptRoot -WindowStyle Normal

Write-Host "[2/3] Waiting for MuJoCo Daemon (http://127.0.0.1:8000) to be ready..." -ForegroundColor Yellow
$daemonReady = $false
$maxAttempts = 30
$attempt = 0

while (-not $daemonReady -and $attempt -lt $maxAttempts) {
    Start-Sleep -Seconds 1
    $attempt++
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/state/full" -TimeoutSec 1 -UseBasicParsing -ErrorAction SilentlyContinue
        if ($response.StatusCode -eq 200) {
            $daemonReady = $true
        }
    } catch {
        # Keep waiting
    }
    Write-Host "." -NoNewline
}
Write-Host ""

if ($daemonReady) {
    Write-Host "MuJoCo Simulation Daemon is ready and running on port 8000!" -ForegroundColor Green
} else {
    Write-Host "[Warning] Daemon wait timed out; attempting to connect..." -ForegroundColor Yellow
}

Write-Host "[3/3] Opening Web Talk UI (http://localhost:7860/#/)..." -ForegroundColor Green
Start-Process "http://localhost:7860/#/"

Write-Host ""
Write-Host "Starting Conversation App with desk_companion_ko profile..." -ForegroundColor Cyan
$env:REACHY_MINI_HOST = "localhost"
$env:REACHY_MINI_PORT = "8000"
$env:REACHY_MINI_CUSTOM_PROFILE = "desk_companion_ko"
$env:CONVERSATION_BACKEND = "openai"

uv run python -m reachy_mini_conversation_app.main --ui
