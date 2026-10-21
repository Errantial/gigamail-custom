# stop_backend.ps1 — run by the installer (and the uninstaller) before any
# file is replaced: ends the GigaMail backend, the python.exe started from
# resources\python under the install folder.
#
# The console leaves the backend running after the window closes, so the
# next start is instant. During an update that process keeps python.exe and
# its DLLs open: the uninstaller cannot delete them, the installer cannot
# overwrite them, and the folder is left half old and half new (0.6.0 to
# 0.6.1 came back up without the gigamail module, and with the old backend
# still holding port 8002). Only python processes running from this very
# folder are touched: any other Python on the machine is left alone.
param([Parameter(Mandatory = $true)][string]$Root)

$ErrorActionPreference = 'SilentlyContinue'
$root = $Root.TrimEnd('\') + '\'
$found = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" |
  Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase) })
foreach ($p in $found) {
  Stop-Process -Id $p.ProcessId -Force
}
if ($found.Count) {
  Wait-Process -Id @($found | ForEach-Object { $_.ProcessId }) -Timeout 15
}
exit 0
