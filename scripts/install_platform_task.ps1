[CmdletBinding()]
param(
    [string]$TaskName = 'SmartTest Platform'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$startScript = Join-Path $PSScriptRoot 'start_platform.ps1'
if (-not (Test-Path -LiteralPath $startScript -PathType Leaf)) {
    throw 'Platform startup script was not found.'
}

$quotedStartScript = '"' + $startScript + '"'
$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File $quotedStartScript -WaitForExit" `
    -WorkingDirectory $repositoryRoot

$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$recoveryTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At ((Get-Date).AddMinutes(1)) `
    -RepetitionInterval (New-TimeSpan -Minutes 5)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew
$settings.IdleSettings.StopOnIdleEnd = $false
$settings.IdleSettings.RestartOnIdle = $true

$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

$task = New-ScheduledTask `
    -Action $action `
    -Trigger @($logonTrigger, $recoveryTrigger) `
    -Settings $settings `
    -Principal $principal `
    -Description 'Keeps the SmartTest frontend and backend running.'

Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null
$registered = Get-ScheduledTask -TaskName $TaskName
if ($registered.State -ne 'Running') {
    Start-ScheduledTask -TaskName $TaskName
}
Write-Output "Scheduled task '$TaskName' is installed with five-minute self-healing."