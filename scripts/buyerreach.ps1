[CmdletBinding()]
param(
  [ValidateSet('start', 'refresh-frontend', 'restart')]
  [string]$Action = 'start'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$frontendRoot = Join-Path $projectRoot 'frontend'
$linuxProjectRoot = (& wsl.exe wslpath -a $projectRoot).Trim()

if (-not $linuxProjectRoot) {
  throw 'Unable to resolve the project path inside WSL.'
}

function Invoke-Compose([string]$command) {
  & wsl.exe sh -lc "cd '$linuxProjectRoot' && docker compose -f docker-compose.yml -f docker-compose.wsl.yml --env-file .env.example $command"
  if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose failed: $command"
  }
}

& wsl.exe sh -lc 'docker version --format "{{.Server.Version}}"' | Out-Null
if ($LASTEXITCODE -ne 0) {
  throw 'Docker Engine is unavailable. Restart WSL/Docker first, then run this command again.'
}

switch ($Action) {
  'start' {
    Invoke-Compose 'up -d --no-build --pull never'
  }
  'refresh-frontend' {
    Push-Location $frontendRoot
    try {
      npm run build
      if ($LASTEXITCODE -ne 0) {
        throw 'Frontend build failed.'
      }
    } finally {
      Pop-Location
    }
    Invoke-Compose 'up -d --build --force-recreate --no-deps frontend'
  }
  'restart' {
    Invoke-Compose 'restart'
  }
}

Start-Sleep -Seconds 2
if ($Action -eq 'refresh-frontend' -or $Action -eq 'start') {
  try {
    $response = Invoke-WebRequest 'http://localhost:5173' -UseBasicParsing -TimeoutSec 10
    if ($response.StatusCode -ne 200) {
      throw "Frontend returned HTTP $($response.StatusCode)."
    }
    Write-Host 'BuyerReach frontend is available at http://localhost:5173'
  } catch {
    throw "Service command completed but the frontend health check failed: $($_.Exception.Message)"
  }
}
