$ErrorActionPreference = 'Stop'
$usagekaniRoot = Split-Path -Parent $PSScriptRoot
$usagekaniPythonw = Join-Path (Split-Path -Parent (Get-Command python.exe).Source) 'pythonw.exe'
Start-Process -FilePath $usagekaniPythonw -ArgumentList '-m companion.usagekani' -WorkingDirectory $usagekaniRoot -WindowStyle Hidden
