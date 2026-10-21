# stop_backend.ps1 — run by the installer (and the uninstaller) before any
# file is replaced: ends the GigaMail backend, the python.exe started from
# resources\python under the install folder.
#
# The console leaves the backend running after the window closes, so the
# next start is instant. During an update that process keeps python.exe and
# its DLLs open: the uninstaller cannot delete them, the installer cannot
# overwrite them, and the folder is left half old and half new (0.6.0 to
# 0.6.1 came back up without the gigamail module, and with the old backend
# still holding port 8002).
#
# Two kinds of process are ended, and nothing else:
#  - python.exe (or pythonw.exe) running from this very install folder;
#  - whatever listens on the console port (8002) and was started as the
#    GigaMail backend ("gigamail.http_api" in its command line), from any
#    folder: a previous install elsewhere, or a backend started from the
#    sources. The new app could not bind the port otherwise. Any other
#    program on that port is left alone.
param([Parameter(Mandatory = $true)][string]$Root)

$ErrorActionPreference = 'SilentlyContinue'
$root = $Root.TrimEnd('\') + '\'
$ids = @()

$here = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" |
  Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase) }
foreach ($p in $here) { $ids += $p.ProcessId }

$port = 8002
if ($env:ADE_CONSOLE_PORT -match '^\d+$') { $port = [int]$env:ADE_CONSOLE_PORT }
$owners = Get-NetTCPConnection -LocalPort $port -State Listen | Select-Object -ExpandProperty OwningProcess -Unique
foreach ($owner in $owners) {
  $p = Get-CimInstance Win32_Process -Filter "ProcessId = $owner"
  if ($p -and $p.CommandLine -and $p.CommandLine -like '*gigamail.http_api*') { $ids += $p.ProcessId }
}

$ids = @($ids | Sort-Object -Unique)
foreach ($id in $ids) { Stop-Process -Id $id -Force }
if ($ids.Count) { Wait-Process -Id $ids -Timeout 15 }
exit 0
