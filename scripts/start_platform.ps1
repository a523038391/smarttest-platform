$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$pythonExecutable = Join-Path $repositoryRoot '.venv\Scripts\python.exe'
$controlRoot = Join-Path $repositoryRoot '.service-control'

if (-not (Test-Path -LiteralPath $pythonExecutable -PathType Leaf)) {
    throw 'Platform Python executable was not found.'
}

foreach ($port in @(8000, 5173)) {
    $listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($null -ne $listener) {
        throw "Platform port $port is already in use."
    }
}

New-Item -ItemType Directory -Path $controlRoot -Force | Out-Null
$env:VERSION_CONTROL_ENABLED = 'true'
$env:VERSION_CONTROL_ROOT = $repositoryRoot

$startArguments = @{
    FilePath = $pythonExecutable
    ArgumentList = @('scripts\platform_supervisor.py')
    WorkingDirectory = $repositoryRoot
    WindowStyle = 'Hidden'
    RedirectStandardOutput = Join-Path $controlRoot 'supervisor.stdout.log'
    RedirectStandardError = Join-Path $controlRoot 'supervisor.stderr.log'
    PassThru = $true
}
$process = Start-Process @startArguments
Write-Output "Platform supervisor started with PID $($process.Id)."