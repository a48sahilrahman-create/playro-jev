<#
.SYNOPSIS
    Play Engine - 1-Click Startup Script for Windows PowerShell
.DESCRIPTION
    Launches the Play Engine Cloud Stremio Addon & Universal OTT Web Player.
    Performs dependency checks, FFmpeg environment setup, port validation, VLC verification, and auto-opens the web UI.
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
    [switch]$NoTunnel,
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
Write-Host " [1/5] Checking Python Environment..." -ForegroundColor DarkGray
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
Write-Host " [2/5] Verifying Required Packages..." -ForegroundColor DarkGray
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

# 5. FFmpeg & HLS Transmuxing Environment Verification
Write-Host " [3/5] Checking FFmpeg Transmuxing Environment..." -ForegroundColor DarkGray
$ffmpegCmd = Get-Command "ffmpeg" -ErrorAction SilentlyContinue
$ffprobeCmd = Get-Command "ffprobe" -ErrorAction SilentlyContinue

$ffmpegFoundPath = $null
if ($ffmpegCmd) {
    $ffmpegFoundPath = $ffmpegCmd.Source
}

if (-not $ffmpegCmd -or -not $ffprobeCmd) {
    $candidatePaths = @(
        "C:\Program Files\Softdeluxe\Free Download Manager\ffmpeg.exe",
        "C:\Program Files (x86)\XDM\ffmpeg.exe",
        "C:\ffmpeg\bin\ffmpeg.exe"
    )
    foreach ($cand in $candidatePaths) {
        if (Test-Path $cand) {
            $candDir = Split-Path -Parent $cand
            if ($env:PATH -split ';' -notcontains $candDir) {
                $env:PATH = "$candDir;$env:PATH"
            }
            if (-not $ffmpegFoundPath) {
                $ffmpegFoundPath = $cand
            }
            break
        }
    }
}

if ($ffmpegFoundPath) {
    Write-Host "       [+] FFmpeg Detected: $ffmpegFoundPath" -ForegroundColor Green
    Write-Host "       [+] Web HLS transmuxing is active." -ForegroundColor Green
} else {
    Write-Host "       [-] FFmpeg not detected in PATH or candidate locations" -ForegroundColor DarkYellow
    Write-Host "       [-] Web player will stream direct media without server-side transmuxing" -ForegroundColor DarkYellow
}

# 6. VLC Media Player Verification
Write-Host " [4/5] Checking VLC Media Player..." -ForegroundColor DarkGray
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

# 7. Port Availability Check
Write-Host " [5/5] Checking Port $Port Availability..." -ForegroundColor DarkGray
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

# 8. Endpoint Summary & LAN IP Discovery
$localIP = $null
try {
    $sock = New-Object System.Net.Sockets.UdpClient
    $sock.Connect("8.8.8.8", 53)
    $localIP = $sock.Client.LocalEndPoint.Address.IPAddressToString
    $sock.Close()
} catch {
    try {
        $ipObj = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
                 Where-Object { $_.IPAddress -notmatch "^127\." -and $_.IPAddress -notmatch "^169\.254\." } |
                 Select-Object -First 1
        if ($ipObj) { $localIP = $ipObj.IPAddress }
    } catch {}
}

if (-not $localIP) {
    $localIP = "127.0.0.1"
}

# Optional Tailscale IP check for remote TV access
$tailscaleIP = $null
try {
    $tsObj = Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias "*Tailscale*" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($tsObj) { $tailscaleIP = $tsObj.IPAddress }
} catch {}

$tvUrl = "http://${localIP}:$Port/?tv=1"

# Copy TV URL to clipboard automatically for convenient pasting
try {
    Set-Clipboard -Value $tvUrl -ErrorAction SilentlyContinue
    $clipSuccess = $true
} catch {
    $clipSuccess = $false
}

# Cloudflare Public Tunnel for Mobile & Remote Zero-PC Access
$cfProcess = $null
$cfUrl = $null
$cfExe = Join-Path $PSScriptRoot ".bin\cloudflared.exe"
if (-not $NoTunnel -and (Test-Path $cfExe)) {
    try {
        $cfLog = Join-Path $env:TEMP "playro_cf_tunnel.log"
        if (Test-Path $cfLog) { Remove-Item $cfLog -Force -ErrorAction SilentlyContinue }
        $cfPsi = New-Object System.Diagnostics.ProcessStartInfo
        $cfPsi.FileName = $cfExe
        $cfPsi.Arguments = "tunnel --url http://127.0.0.1:$Port"
        $cfPsi.RedirectStandardError = $true
        $cfPsi.RedirectStandardOutput = $true
        $cfPsi.UseShellExecute = $false
        $cfPsi.CreateNoWindow = $true
        $cfProcess = New-Object System.Diagnostics.Process
        $cfProcess.StartInfo = $cfPsi
        $cfProcess.Start() | Out-Null

        # Spawn background reader to dump log
        $cfStreamWatcher = [PowerShell]::Create().AddScript({
            param($proc, $logFile)
            while (-not $proc.HasExited) {
                $line = $proc.StandardError.ReadLine()
                if ($line) { Add-Content -Path $logFile -Value $line -Encoding utf8 }
            }
        }).AddArgument($cfProcess).AddArgument($cfLog)
        $null = $cfStreamWatcher.BeginInvoke()

        # Poll up to 4s for public trycloudflare URL
        for ($k = 0; $k -lt 20; $k++) {
            Start-Sleep -Milliseconds 200
            if (Test-Path $cfLog) {
                $match = Select-String -Path $cfLog -Pattern 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' | Select-Object -First 1
                if ($match) {
                    $cfUrl = $match.Matches[0].Value
                    break
                }
            }
        }
    } catch {}
}

Write-Host ""
Write-Host " -------------------------------------------------------------------------" -ForegroundColor DarkCyan
Write-Host "   >> Access URLs:" -ForegroundColor White
Write-Host "      * Local PC Player:      http://localhost:$Port/" -ForegroundColor Cyan
Write-Host "      * Google / Android TV:  $tvUrl" -ForegroundColor Green
Write-Host "      * Phone / LAN Browser:  http://${localIP}:$Port/" -ForegroundColor Yellow
if ($cfUrl) {
Write-Host "      * Cloudflare Tunnel:    $cfUrl" -ForegroundColor Cyan
}
if ($tailscaleIP) {
Write-Host "      * Remote (Tailscale):   http://${tailscaleIP}:$Port/?tv=1" -ForegroundColor DarkYellow
}
Write-Host "      * Stremio TV Manifest:  http://${localIP}:$Port/manifest.json" -ForegroundColor Magenta
Write-Host "      * Interactive API Docs: http://localhost:$Port/docs" -ForegroundColor DarkGray
Write-Host " -------------------------------------------------------------------------" -ForegroundColor DarkCyan
if ($clipSuccess) {
Write-Host "   [+] TV URL auto-copied to Clipboard! Paste directly into TV/Phone remote." -ForegroundColor Green
}
Write-Host "   >> TIP: Type or paste the 'Google / Android TV' URL into your TV browser" -ForegroundColor White
Write-Host "           (TV Bro, Chrome, JioPages, Silk) for 10-foot D-Pad & Voice search." -ForegroundColor DarkGray
Write-Host "   Press Ctrl+C in this window to stop the server at any time." -ForegroundColor DarkGray
Write-Host ""

# 9. Background Browser Launcher
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

# 10. Start Uvicorn Server
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
    if ($cfProcess -and -not $cfProcess.HasExited) {
        Stop-Process -Id $cfProcess.Id -Force -ErrorAction SilentlyContinue
    }
    Write-Host "`n[+] Play Engine Server stopped cleanly." -ForegroundColor Yellow
}
