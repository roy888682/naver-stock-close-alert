$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

try {
    $venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython)) {
        $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
        if ($launcher) {
            & $launcher.Source -3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'
            if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 or newer is required. Install it from https://www.python.org/downloads/windows/.' }
            & $launcher.Source -3 -m venv .venv
        } else {
            $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
            if (-not $pythonCommand) { throw 'Python 3.11 or newer was not found. Install it and enable Add python.exe to PATH.' }
            & $pythonCommand.Source -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'
            if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 or newer is required. Install it from https://www.python.org/downloads/windows/.' }
            & $pythonCommand.Source -m venv .venv
        }
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python virtual environment.' }
    }

    & $venvPython -c 'import tzdata'
    if ($LASTEXITCODE -ne 0) {
        & $venvPython -m pip install -r requirements.txt
        if ($LASTEXITCODE -ne 0) { throw 'Could not install requirements. Check your Internet connection and try again.' }
    }

    if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.env'))) {
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot '.env.example') -Destination (Join-Path $PSScriptRoot '.env')
        Write-Host 'Created .env. Set SMTP_PASSWORD before sending mail. Restart the program after editing .env.'
    }

    Write-Host 'Opening the stock alert dashboard at http://127.0.0.1:8765'
    Write-Host 'Keep this window open for automatic checks. Press Ctrl+C to stop.'
    & $venvPython -m stock_alert @args
    exit $LASTEXITCODE
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
