function Read-BridgeConfiguration {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [switch]$RequireTunnelToken
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw 'Missing project .env; copy .env.example and fill in the local settings.'
    }

    $settings = @{}
    $lineNumber = 0
    foreach ($line in [System.IO.File]::ReadAllLines($Path)) {
        $lineNumber++
        if ($line -match '^\s*(#.*)?$') { continue }
        if ($line -notmatch '^([A-Z][A-Z0-9_]*)=(.*)$') {
            throw "Invalid .env syntax at line $lineNumber; use literal KEY=value."
        }
        $key = $Matches[1]
        $value = $Matches[2]
        if ($settings.ContainsKey($key)) {
            throw "Duplicate .env setting: $key."
        }
        if ($value -ne $value.Trim() -or $value -match '^["'']|["'']$') {
            throw "Use an unquoted literal value for .env setting $key."
        }
        $settings[$key] = $value
    }

    foreach ($key in @('LOCAL_SERVER_HOST', 'LOCAL_SERVER_PORT',
                       'LOCAL_SERVER_PUBLIC_ORIGIN', 'WSL_DISTRIBUTION')) {
        if ([string]::IsNullOrWhiteSpace($settings[$key])) {
            throw "Missing .env setting: $key."
        }
    }
    if ($settings['LOCAL_SERVER_HOST'] -cne '127.0.0.1') {
        throw 'LOCAL_SERVER_HOST must be 127.0.0.1; public binding is not supported.'
    }
    $listenPort = 0
    if (-not [int]::TryParse($settings['LOCAL_SERVER_PORT'], [ref]$listenPort) -or
        $listenPort -lt 1 -or $listenPort -gt 65535) {
        throw 'LOCAL_SERVER_PORT must be between 1 and 65535.'
    }
    $origin = $null
    if (-not [uri]::TryCreate($settings['LOCAL_SERVER_PUBLIC_ORIGIN'],
                             [System.UriKind]::Absolute, [ref]$origin) -or
        $origin.Scheme -cne 'https' -or $origin.UserInfo -or
        $origin.AbsolutePath -ne '/' -or $origin.Query -or $origin.Fragment) {
        throw 'LOCAL_SERVER_PUBLIC_ORIGIN must be one HTTPS origin without credentials or a path.'
    }
    if ($RequireTunnelToken -and [string]::IsNullOrWhiteSpace($settings['TUNNEL_TOKEN'])) {
        throw 'Missing .env setting: TUNNEL_TOKEN.'
    }

    [pscustomobject]@{
        ListenHost = $settings['LOCAL_SERVER_HOST']
        Port = $listenPort
        PublicOrigin = $settings['LOCAL_SERVER_PUBLIC_ORIGIN']
        Distribution = $settings['WSL_DISTRIBUTION']
    }
}
