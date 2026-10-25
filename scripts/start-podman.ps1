$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$connection = 'podman-machine-default-root'
if (-not (Test-Path -LiteralPath '.env')) { throw 'Copy .env.example to .env and set TSE_CONTACT_EMAIL first.' }
podman.exe --connection $connection info | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Start existing machine: podman.exe machine start podman-machine-default' }
podman.exe --connection $connection build --format docker -t localhost/eleicoes2026:current .
if ($LASTEXITCODE -ne 0) { throw 'Image build failed' }
podman.exe --connection $connection network exists eleicoes2026-net
if ($LASTEXITCODE -ne 0) { podman.exe --connection $connection network create eleicoes2026-net }
podman.exe --connection $connection volume exists eleicoes2026-data
if ($LASTEXITCODE -ne 0) { podman.exe --connection $connection volume create eleicoes2026-data }
podman.exe --connection $connection container exists eleicoes2026-dashboard
if ($LASTEXITCODE -eq 0) { throw 'Existing dashboard preserved. Use README staged update steps; this script never deletes it.' }
podman.exe --connection $connection run -d --name eleicoes2026-dashboard --network eleicoes2026-net --restart unless-stopped --cap-drop ALL --security-opt no-new-privileges --env-file .env -v eleicoes2026-data:/data -p 127.0.0.1:18501:8501 -p 127.0.0.1:18502:8080 localhost/eleicoes2026:current
if ($LASTEXITCODE -ne 0) { throw 'Container startup failed' }
