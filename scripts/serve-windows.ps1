param(
    [switch]$LocalOnly
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'config-windows.ps1')
$configuration = Read-BridgeConfiguration -Path (Join-Path $projectRoot '.env') `
    -RequireTunnelToken:(-not $LocalOnly)
$linuxRoot = & wsl.exe --distribution $configuration.Distribution --exec wslpath -a -u $projectRoot
if ($LASTEXITCODE -ne 0) {
    throw 'Cannot resolve project path in the configured WSL distribution.'
}
$linuxRoot = $linuxRoot.Trim()

& wsl.exe --distribution $configuration.Distribution --cd $linuxRoot --exec test -x .venv/bin/python
if ($LASTEXITCODE -ne 0) {
    throw 'Install the locked Python environment in WSL first; see docs/windows-deployment.md.'
}
& wsl.exe --distribution $configuration.Distribution --cd $linuxRoot --exec test -f lib/libdds.so
if ($LASTEXITCODE -ne 0) {
    throw 'Build DDS in WSL first: python scripts/build_dds.py'
}

$serveArgs = @('serve', '--port', "$($configuration.Port)")
if (-not $LocalOnly) {
    # env_file: raw reads the project file directly, without shell interpolation.
    # Do not print `docker compose config`, which expands the private token.
    & docker compose --project-name bridge-dealer --project-directory $projectRoot `
        --file (Join-Path $projectRoot 'docker-compose.yml') `
        --env-file (Join-Path $projectRoot '.env') up -d --pull missing cloudflared
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not start the BridgeDealer Cloudflare connector.'
    }
    $serveArgs += @('--public-origin', $configuration.PublicOrigin)
}
& wsl.exe --distribution $configuration.Distribution --cd $linuxRoot --exec env `
    "XDG_CACHE_HOME=$linuxRoot/.cache" "MPLCONFIGDIR=$linuxRoot/.cache/matplotlib" `
    .venv/bin/python -m bridge_dealer @serveArgs
exit $LASTEXITCODE
