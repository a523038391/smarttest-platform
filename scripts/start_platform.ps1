[CmdletBinding()]
param(
    [switch]$WaitForExit
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$pythonExecutable = Join-Path $repositoryRoot '.venv\Scripts\python.exe'
$controlRoot = Join-Path $repositoryRoot '.service-control'
$protectedKeyPath = Join-Path $controlRoot 'secret-encryption-key.dpapi'

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
if (-not (Test-Path -LiteralPath $protectedKeyPath -PathType Leaf)) {
    $keyBytes = New-Object byte[] 32
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($keyBytes)
        $encodedKey = [Convert]::ToBase64String($keyBytes)
        $protectedKey = ConvertFrom-SecureString (ConvertTo-SecureString $encodedKey -AsPlainText -Force)
        Set-Content -LiteralPath $protectedKeyPath -Value $protectedKey -Encoding ASCII -NoNewline
    } finally {
        $generator.Dispose()
        [Array]::Clear($keyBytes, 0, $keyBytes.Length)
    }
}
$secureKey = ConvertTo-SecureString (Get-Content -LiteralPath $protectedKeyPath -Raw)
$keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
try {
    $env:SECRET_ENCRYPTION_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer)
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
}
$env:SECRET_ENCRYPTION_KEY_ID = 'windows-dpapi-v1'
$env:VERSION_CONTROL_ENABLED = 'true'
$env:VERSION_CONTROL_ROOT = $repositoryRoot
$env:RUNNER_NETWORK = 'bridge'

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
if ($WaitForExit) {
    $process.WaitForExit()
    exit $process.ExitCode
}