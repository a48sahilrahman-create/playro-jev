<#
.SYNOPSIS
    Play Engine - 1-Click Startup Script for Windows PowerShell
.DESCRIPTION
    Launches the Play Engine Cloud Stremio Addon & Universal OTT Web Player.
    Performs dependency checks, port validation, VLC verification, and auto-opens the web UI.
.PARAMETER Port
    Port to bind the server on (Default: 7860)
.PARAMETER HostAddress
    Host address to bind on (Default: 0.0.0.0)
.PARAMETER NoBrowser
    If specified, suppresses opening the default browser automatically.
.PARAMETER Reload
    If specified, enables uvicorn live code reloading for development.
.EXAMPLE
    .\start.ps1
.EXAMPLE
    .\start.ps1 -Port 8000 -Reload
#>

[CmdletBinding()]
param (
    [int]$Port = 7860,
    [string]$HostAddress = "0.0.0.0",
    [switch]$NoBrowser,
    [switch]$Reload
)

# 1. Set Working Directory to Script Location
Set-Location -Path $PSScriptRoot

# Set Window Title safely if supported
try {
    if ($Host -and $Host.UI -and $Host.UI.RawUI) {
        $Host.UI.RawUI.WindowTitle = "Play Engine - Universal Stremio and OTT Player (Port $Port)"
    }
} catch {
    # Headless / piped ignore
}

# 2. Visual Banner
Write-Host ""
Write-Host " =========================================================================" -ForegroundColor Cyan
Write-Host "   [*] PLAY ENGINE - CLOUD STREMIO ADDON & UNIVERSAL OTT WEB PLAYER" -ForegroundColor Yellow
Write-Host " =========================================================================" -ForegroundColor Cyan
Write-Host ""

# 3. Environment & Python Validation
Write-Host " [1/4] Checking Python Environment..." -ForegroundColor DarkGray
$pythonCmd = $null
if (Get-Command "python" -ErrorAction SilentlyContinue) {
    $pythonCmd = "python"
} elseif (Get-Command "py" -ErrorAction SilentlyContinue) {
    $pythonCmd = "py"
} else {
    Write-Host " [!] ERROR: Python is not installed or not in system PATH." -ForegroundColor Red
    Write-Host "     Please install Python 3.10+ from https://www.python.org/" -ForegroundColor Yellow
    exit 1
}

$pyVersion = & $pythonCmd --version 2>&1
Write-Host "       [+] Python Detected: $pyVersion" -ForegroundColor Green

# Activate virtual environment if present
if (Test-Path "$PSScriptRoot\.venv\Scripts\Activate.ps1") {
    Write-Host "       [+] Activating Virtual Environment (.venv)..." -ForegroundColor Green
    & "$PSScriptRoot\.venv\Scripts\Activate.ps1"
} elseif (Test-Path "$PSScriptRoot\venv\Scripts\Activate.ps1") {
    Write-Host "       [+] Activating Virtual Environment (venv)..." -ForegroundColor Green
    & "$PSScriptRoot\venv\Scripts\Activate.ps1"
}

# 4. Dependency Validation
Write-Host " [2/4] Verifying Required Packages..." -ForegroundColor DarkGray
$missingDeps = $false
try {
    & $pythonCmd -c "import fastapi, uvicorn, httpx, bs4, lxml, pydantic" 2>$null
    if ($LASTEXITCODE -ne 0) { $missingDeps = $true }
} catch {
    $missingDeps = $true
}

if ($missingDeps) {
    Write-Host "       [!] Missing dependencies detected. Installing from requirements.txt..." -ForegroundColor Yellow
    & $pythonCmd -m pip install -r "$PSScriptRoot\requirements.txt"
    if ($LASTEXITCODE -ne 0) {
        Write-Host " [!] Failed to install dependencies." -ForegroundColor Red
        exit 1
    }
    Write-Host "       [+] Dependencies installed successfully." -ForegroundColor Green
} else {
    Write-Host "       [+] All core dependencies verified." -ForegroundColor Green
}

# 5. VLC Media Player Verification
Write-Host " [3/4] Checking VLC Media Player..." -ForegroundColor DarkGray
$vlcPaths = @(
    "C:\Program Files\VideoLAN\VLC\vlc.exe",
    "C:\Program Files (x86)\VideoLAN\VLC\vlc.exe"
)
$vlcFound = $false
foreach ($path in $vlcPaths) {
    if (Test-Path $path) {
        Write-Host "       [+] VLC Media Player Detected ($path)" -ForegroundColor Green
        $vlcFound = $true
        break
    }
}
if (-not $vlcFound) {
    Write-Host "       [-] VLC not in default path (Web player and browser streaming will still work)" -ForegroundColor DarkYellow
}

# 6. Port Availability Check
Write-Host " [4/4] Checking Port $Port Availability..." -ForegroundColor DarkGray
$listeners = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
$inUse = $listeners | Where-Object { $_.Port -eq $Port }

if ($inUse) {
    Write-Host "       [!] Port $Port is currently in use." -ForegroundColor Yellow
    try {
        $conns = Get-CimInstance -ClassName MSFT_NetTCPConnection -Namespace root\StandardCimv2 -Filter "LocalPort = $Port" -ErrorAction SilentlyContinue
        if ($conns) {
            $pids = $conns | Select-Object -ExpandProperty OwningProcess -Unique
            foreach ($p in $pids) {
                Write-Host "       [+] Terminating occupying process (PID: $p)..." -ForegroundColor Yellow
                Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
            }
            Start-Sleep -Milliseconds 500
        }
    } catch {
        # Fallback netstat check if needed
    }
    Write-Host "       [+] Port $Port released and ready." -ForegroundColor Green
} else {
    Write-Host "       [+] Port $Port is available." -ForegroundColor Green
}

# 7. Endpoint Summary
Write-Host ""
Write-Host " -------------------------------------------------------------------------" -ForegroundColor DarkCyan
Write-Host "   >> Access URLs:" -ForegroundColor White
Write-Host "      * Web OTT Player:       http://localhost:$Port/" -ForegroundColor Cyan
Write-Host "      * Stremio Configure:    http://localhost:$Port/configure" -ForegroundColor Cyan
Write-Host "      * Stremio Manifest:     http://localhost:$Port/manifest.json" -ForegroundColor Cyan
Write-Host "      * Interactive API Docs: http://localhost:$Port/docs" -ForegroundColor Cyan
Write-Host " -------------------------------------------------------------------------" -ForegroundColor DarkCyan
Write-Host "   Press Ctrl+C in this window to stop the server at any time." -ForegroundColor DarkGray
Write-Host ""

# 8. Background Browser Launcher
$browserJob = $null
if (-not $NoBrowser) {
    $browserUrl = "http://localhost:$Port/"
    $browserJob = Start-Job -ScriptBlock {
        param($url, $checkPort)
        $maxAttempts = 30
        for ($i = 0; $i -lt $maxAttempts; $i++) {
            Start-Sleep -Milliseconds 400
            try {
                $client = New-Object System.Net.Sockets.TcpClient
                $iar = $client.BeginConnect("127.0.0.1", $checkPort, $null, $null)
                $success = $iar.AsyncWaitHandle.WaitOne(300, $false)
                if ($success -and $client.Connected) {
                    $client.EndConnect($iar)
                    $client.Close()
                    Start-Process $url
                    break
                }
                $client.Close()
            } catch {
                # Continue waiting
            }
        }
    } -ArgumentList $browserUrl, $Port
}

# 9. Start Uvicorn Server
$env:PORT = "$Port"

try {
    if ($Reload) {
        & $pythonCmd -m uvicorn main:app --host $HostAddress --port $Port --reload
    } else {
        & $pythonCmd -m uvicorn main:app --host $HostAddress --port $Port
    }
} catch {
    Write-Host "`n[!] Server exited with message: $_" -ForegroundColor Red
} finally {
    if ($browserJob) {
        Stop-Job -Job $browserJob -ErrorAction SilentlyContinue
        Remove-Job -Job $browserJob -ErrorAction SilentlyContinue
    }
    Write-Host "`n[+] Play Engine Server stopped cleanly." -ForegroundColor Yellow
}
