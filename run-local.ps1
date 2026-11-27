$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    $uvInstallDirectory = Join-Path $HOME ".local\bin"
    if (Test-Path -LiteralPath $uvInstallDirectory) {
        $env:PATH = "$uvInstallDirectory;$env:PATH"
    }
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv was not found. Install uv or add it to PATH, then run this script again."
}

uv python install 3.12
if ($LASTEXITCODE -ne 0) { throw "uv could not install Python 3.12." }

uv venv .venv --python 3.12
if ($LASTEXITCODE -ne 0) { throw "uv could not create the virtual environment." }

uv pip install --python .venv\Scripts\python.exe -r sut_app\requirements.txt
if ($LASTEXITCODE -ne 0) { throw "uv could not install the application requirements." }

if ([string]::IsNullOrWhiteSpace($env:APP_API_KEY)) {
    $apiKey = & .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($apiKey)) {
        throw "Could not generate the local API key."
    }
    $env:APP_API_KEY = $apiKey.Trim()
}
if ([string]::IsNullOrWhiteSpace($env:SESSION_COOKIE_SECURE)) {
    $env:SESSION_COOKIE_SECURE = "false"
}
if ([string]::IsNullOrWhiteSpace($env:ASSISTANT_USERNAME)) {
    $env:ASSISTANT_USERNAME = "demo-admin"
}
if ([string]::IsNullOrWhiteSpace($env:ASSISTANT_PASSWORD)) {
    $env:ASSISTANT_PASSWORD = "choose-a-long-local-password"
}
if ([string]::IsNullOrWhiteSpace($env:ASSISTANT_SESSION_SECRET)) {
    $sessionSecret = & .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($sessionSecret)) {
        throw "Could not generate the assistant session secret."
    }
    $env:ASSISTANT_SESSION_SECRET = $sessionSecret.Trim()
}
if ([System.Text.Encoding]::UTF8.GetByteCount($env:ASSISTANT_SESSION_SECRET) -lt 32) {
    throw "ASSISTANT_SESSION_SECRET must be at least 32 characters."
}

$port = 8000
if (-not [string]::IsNullOrWhiteSpace($env:APP_PORT)) {
    $customPort = 0
    if (-not [int]::TryParse($env:APP_PORT, [ref]$customPort) -or $customPort -lt 1 -or $customPort -gt 65535) {
        throw "APP_PORT must be an integer between 1 and 65535."
    }
    $port = $customPort
}

Write-Host "Local assistant username: $env:ASSISTANT_USERNAME"
Write-Host "Open http://localhost:$port (Ctrl+C to stop the app)."

& .\.venv\Scripts\python.exe -m uvicorn main:app --app-dir sut_app --host 127.0.0.1 --port $port
if ($LASTEXITCODE -ne 0) { throw "The local app stopped with exit code $LASTEXITCODE." }
