param([string]$HostAddress)
$ErrorActionPreference = 'Stop'
$usagekaniRoot = Split-Path -Parent $PSScriptRoot
$usagekaniPython = (Get-Command python.exe).Source
Push-Location $usagekaniRoot
try {
    if ($HostAddress) { & $usagekaniPython -m companion.usagekani.install --host $HostAddress }
    else { & $usagekaniPython -m companion.usagekani.install }
    if ($LASTEXITCODE -ne 0) { throw 'Companion installation failed.' }
    $usagekaniState = Join-Path $env:LOCALAPPDATA 'Usagekani'
    $usagekaniPythonw = Join-Path (Split-Path -Parent $usagekaniPython) 'pythonw.exe'
    if (-not (Test-Path -LiteralPath $usagekaniPythonw)) { throw 'pythonw.exe is required for background startup.' }
    $usagekaniShell = New-Object -ComObject WScript.Shell
    $usagekaniShortcut = $usagekaniShell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Startup')) 'Usagekani.lnk'))
    $usagekaniShortcut.TargetPath = $usagekaniPythonw
    $usagekaniShortcut.Arguments = '-m companion.usagekani'
    $usagekaniShortcut.WorkingDirectory = $usagekaniRoot
    $usagekaniShortcut.WindowStyle = 7
    $usagekaniShortcut.Description = 'Usagekani local usage companion'
    $usagekaniShortcut.Save()
    # Reuse Wi-Fi values locally; no credential values are printed or committed.
    $usagekaniLocal = Join-Path $usagekaniRoot 'hal_firmware/Core/Inc/app_config_local.h'
    $usagekaniConfig = Get-Content -LiteralPath (Join-Path $usagekaniState 'config.json') -Raw | ConvertFrom-Json
    if (Test-Path -LiteralPath $usagekaniLocal) { $usagekaniHeader = Get-Content -LiteralPath $usagekaniLocal -Raw }
    else { $usagekaniHeader = "/* Local configuration: never commit this file. */`n" }
    foreach ($usagekaniDefinition in @(
        @('USAGEKANI_HTTP_HOST', $usagekaniConfig.host),
        @('USAGEKANI_HTTP_PORT', [string]$usagekaniConfig.port),
        @('USAGEKANI_DEVICE_TOKEN', $usagekaniConfig.device_token)
    )) {
        $usagekaniName = $usagekaniDefinition[0]
        $usagekaniValue = $usagekaniDefinition[1]
        $usagekaniHeader = [regex]::Replace($usagekaniHeader, ('(?m)^\s*#define\s+' + $usagekaniName + '\s+.*$'), '')
        $usagekaniHeader += "`n#define $usagekaniName `"$usagekaniValue`"`n"
    }
    [System.IO.File]::WriteAllText($usagekaniLocal, $usagekaniHeader, (New-Object System.Text.UTF8Encoding $false))
    # Optional __has_include headers are absent from GCC's old dependency files.
    # Touch the always-included parent so every consumer discovers the new file.
    (Get-Item -LiteralPath (Join-Path $usagekaniRoot 'hal_firmware/Core/Inc/app_config.h')).LastWriteTimeUtc = [DateTime]::UtcNow
    Write-Output 'Configured Claude status line, local firmware settings, and Windows login startup.'
} finally { Pop-Location }
