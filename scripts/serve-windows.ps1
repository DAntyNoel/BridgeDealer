param(
    [string]$Distribution = 'Ubuntu',
    [ValidateRange(1, 65535)]
    [int]$Port = 12707,
    [string]$PublicOrigin = 'https://bridge.kanami.top',
    [switch]$LocalOnly
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$linuxRoot = & wsl.exe --distribution $Distribution --exec wslpath -a -u $projectRoot
if ($LASTEXITCODE -ne 0) {
    throw "Cannot resolve project path in WSL distribution '$Distribution'."
}
$linuxRoot = $linuxRoot.Trim()

& wsl.exe --distribution $Distribution --cd $linuxRoot --exec test -x .venv/bin/python
if ($LASTEXITCODE -ne 0) {
    throw 'Install the locked Python environment in WSL first; see docs/windows-deployment.md.'
}
& wsl.exe --distribution $Distribution --cd $linuxRoot --exec test -f lib/libdds.so
if ($LASTEXITCODE -ne 0) {
    throw 'Build DDS in WSL first: python scripts/build_dds.py'
}

$serveArgs = @('serve', '--port', "$Port")
if (-not $LocalOnly) {
    $serveArgs += @('--public-origin', $PublicOrigin)
}
& wsl.exe --distribution $Distribution --cd $linuxRoot --exec env `
    "XDG_CACHE_HOME=$linuxRoot/.cache" "MPLCONFIGDIR=$linuxRoot/.cache/matplotlib" `
    .venv/bin/python -m bridge_dealer @serveArgs
exit $LASTEXITCODE
