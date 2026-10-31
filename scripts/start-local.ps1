[CmdletBinding()]
param(
    [switch]$DryRun,
    [ValidateSet("full", "host")]
    [string]$Mode = "full",
    [switch]$SkipBuild,
    [switch]$WithRanking,
    [switch]$RunE2E
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$logsDirectory = Join-Path $projectRoot "logs"
$composeFile = Join-Path $projectRoot "docker/docker-compose.yaml"
$envFile = Join-Path $projectRoot ".env"
$composeCommandArguments = @("--env-file", $envFile, "-f", $composeFile)

function Test-ListeningPort {
    param([int]$Port)

    return $null -ne (
        Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
            Select-Object -First 1
    )
}

function Start-ProjectProcess {
    param(
        [string]$Name,
        [string]$WorkingDirectory,
        [string]$Executable,
        [string[]]$Arguments,
        [int]$Port
    )

    if (Test-ListeningPort -Port $Port) {
        Write-Host "$Name already listens on port $Port."
        return
    }

    if ($DryRun) {
        Write-Host "Would start $Name on port $Port."
        return
    }

    $standardOutput = Join-Path $logsDirectory "$Name.out.log"
    $standardError = Join-Path $logsDirectory "$Name.err.log"
    $process = Start-Process -FilePath $Executable -ArgumentList $Arguments `
        -WorkingDirectory $WorkingDirectory -RedirectStandardOutput $standardOutput `
        -RedirectStandardError $standardError -WindowStyle Hidden -PassThru
    Write-Host "$Name started with PID $($process.Id). Logs: $standardOutput"
}

Set-Location $projectRoot

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker CLI is not installed or is unavailable in PATH."
}

function Wait-DockerEngine {
    param([int]$TimeoutSeconds = 180)

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $ready = $false
        try {
            & docker info --format "{{.ServerVersion}}" *> $null
            $ready = ($LASTEXITCODE -eq 0)
        } catch {
            $ready = $false
        }
        if ($ready) {
            return
        }
        Start-Sleep -Seconds 3
    } while ((Get-Date) -lt $deadline)

    throw "Docker engine did not become ready within $TimeoutSeconds seconds. Start Docker Desktop and retry."
}

if ($DryRun) {
    Write-Host "Would ensure Docker Desktop is running and the Docker engine is ready."
} else {
    $dockerReady = $false
    try {
        & docker info --format "{{.ServerVersion}}" *> $null
        $dockerReady = ($LASTEXITCODE -eq 0)
    } catch {
        $dockerReady = $false
    }
    if (-not $dockerReady) {
        Write-Host "Docker engine is unavailable; asking Docker Desktop to start."
        try {
            & docker desktop start --timeout 180
        } catch {
            Write-Warning "Docker Desktop start command failed: $($_.Exception.Message)"
        }
        Wait-DockerEngine
    }
}

if ($Mode -eq "full") {
    $profileArguments = @("--profile", "full", "--profile", "state")
    if ($WithRanking) {
        $profileArguments += @("--profile", "ranking")
    }
    $composeArguments = @(
        "compose"
    ) + $composeCommandArguments + $profileArguments + @(
        "up", "-d"
    )
    $imageReady = $false
    if ($SkipBuild) {
        $imageReady = $true
        $composeImages = @(
            & docker compose @composeCommandArguments @profileArguments config --images 2>$null
        )
        $composeProjectName = (& docker compose @composeCommandArguments @profileArguments config --format json | ConvertFrom-Json).name
        foreach ($imageName in @(
            "$composeProjectName-backend",
            "$composeProjectName-mcp",
            "$composeProjectName-frontend"
        )) {
            $builtImage = $composeImages | Where-Object {
                "$_".Trim() -eq $imageName
            } | Select-Object -First 1
            if (-not $builtImage) {
                $imageReady = $false
                break
            }
            try {
                & docker image inspect "$builtImage".Trim() *> $null
                if ($LASTEXITCODE -ne 0) {
                    $imageReady = $false
                    break
                }
            } catch {
                $imageReady = $false
                break
            }
        }
        if (-not $imageReady) {
            Write-Host "Application images are missing; building them before startup."
        }
    }
    if (-not $SkipBuild -or -not $imageReady) {
        $composeArguments += "--build"
    }
    if ($RunE2E -and -not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
        throw "pnpm is required to run the Playwright end-to-end checks."
    }
    if ($DryRun) {
        Write-Host ("Would run: docker " + ($composeArguments -join " "))
        Write-Host "Would wait for backend, MCP and frontend readiness before opening http://localhost:5173."
    } else {
        & docker @composeArguments
        if ($LASTEXITCODE -ne 0) {
            throw "Docker Compose full application failed to start."
        }
    }

    if ($DryRun) {
        return
    }

    $services = @("backend", "mcp", "frontend")
    $deadline = (Get-Date).AddMinutes(8)
    do {
        $pending = @()
        foreach ($service in $services) {
            $containerId = (& docker compose @composeCommandArguments @profileArguments ps -q $service).Trim()
            if (-not $containerId) {
                $pending += "$service(not-created)"
                continue
            }
            $health = (& docker inspect --format "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}" $containerId).Trim()
            if ($health -ne "healthy") {
                $pending += "$service($health)"
            }
        }
        if ($pending.Count -eq 0) {
            break
        }
        Write-Host ("Waiting for services: " + ($pending -join ", " ))
        Start-Sleep -Seconds 10
    } while ((Get-Date) -lt $deadline)

    if ($pending.Count -gt 0) {
        throw "Services did not become healthy: $($pending -join ', '). Check docker compose ps and container logs."
    }

    Write-Host "Full workbench is healthy. Open http://localhost:5173"
    if ($RunE2E) {
        $adminEmail = $null
        $adminPassword = $null
        $envFile = Join-Path $projectRoot ".env"
        if (Test-Path -LiteralPath $envFile) {
            foreach ($line in Get-Content -LiteralPath $envFile) {
                if ($line -match '^APP_ADMIN_EMAIL=(.*)$') {
                    $adminEmail = $matches[1].Trim()
                }
                if ($line -match '^APP_ADMIN_PASSWORD=(.*)$') {
                    $adminPassword = $matches[1].Trim()
                }
            }
        }
        if (-not $adminEmail -or -not $adminPassword) {
            throw "APP_ADMIN_EMAIL and APP_ADMIN_PASSWORD are required for Playwright checks."
        }

        $chromePath = Join-Path $env:ProgramFiles "Google/Chrome/Application/chrome.exe"
        $savedE2EEnvironment = @{
            E2E_ADMIN_EMAIL = $env:E2E_ADMIN_EMAIL
            E2E_ADMIN_PASSWORD = $env:E2E_ADMIN_PASSWORD
            E2E_BASE_URL = $env:E2E_BASE_URL
            E2E_CHROME_PATH = $env:E2E_CHROME_PATH
        }
        try {
            $env:E2E_ADMIN_EMAIL = $adminEmail
            $env:E2E_ADMIN_PASSWORD = $adminPassword
            $env:E2E_BASE_URL = "http://127.0.0.1:5173"
            if (Test-Path -LiteralPath $chromePath) {
                $env:E2E_CHROME_PATH = $chromePath
            }
            Push-Location (Join-Path $projectRoot "frontend")
            & pnpm run test:e2e
            if ($LASTEXITCODE -ne 0) {
                throw "Playwright end-to-end checks failed."
            }
        } finally {
            Pop-Location
            foreach ($name in $savedE2EEnvironment.Keys) {
                Set-Item -Path "Env:$name" -Value $savedE2EEnvironment[$name]
            }
        }
        Write-Host "Playwright end-to-end checks passed."
    }
    return
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is not installed or is unavailable in PATH."
}
if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
    throw "pnpm is not installed or is unavailable in PATH."
}

if ($DryRun) {
    Write-Host "Would start MySQL, Qdrant, Elasticsearch, Kibana and Embedding with docker/docker-compose.yaml."
} else {
    & docker compose @composeCommandArguments up -d
    if ($LASTEXITCODE -ne 0) {
        throw "Docker infrastructure failed to start."
    }
}

if (-not $DryRun) {
    New-Item -ItemType Directory -Force -Path $logsDirectory | Out-Null
}

$uvPath = (Get-Command uv).Source
$pnpmPath = (Get-Command pnpm).Source
Start-ProjectProcess -Name "backend" -WorkingDirectory $projectRoot -Executable $uvPath `
    -Arguments @("run", "fastapi", "dev", "main.py") -Port 8000
Start-ProjectProcess -Name "frontend" -WorkingDirectory (Join-Path $projectRoot "frontend") `
    -Executable $pnpmPath -Arguments @("dev") -Port 5173

if ($DryRun) {
    Write-Host "Would open http://localhost:5173 after services are ready."
} else {
    Write-Host "Local workbench startup requested. Open http://localhost:5173 after the services report ready."
}
