// The installer ends the backend before it replaces a file: an update over
// a live python.exe left resources\python half replaced (0.6.0 to 0.6.1).
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const nsh = fs.readFileSync(path.join(root, 'installer', 'installer.nsh'), 'utf8');
const ps1 = fs.readFileSync(path.join(root, 'installer', 'stop_backend.ps1'), 'utf8');

test('electron-builder includes the hook script', () => {
  const pkg = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));
  assert.equal(pkg.build.nsis.include, 'installer/installer.nsh');
});

test('installer and uninstaller stop the backend before touching files', () => {
  assert.match(nsh, /!macro customInit\b/);
  assert.match(nsh, /!macro customUnInit\b/);
  assert.match(nsh, /File \/oname=\$PLUGINSDIR\\stop_backend\.ps1 "\$\{PROJECT_DIR\}\\installer\\stop_backend\.ps1"/);
  assert.match(nsh, /powershell\.exe .*-File "\$PLUGINSDIR\\stop_backend\.ps1" "\$INSTDIR"/);
});

test('only python processes running from the install folder are ended', () => {
  assert.match(ps1, /Name = 'python\.exe'/);
  assert.match(ps1, /ExecutablePath\.StartsWith\(\$root/);
  assert.match(ps1, /Stop-Process -Id \$p\.ProcessId -Force/);
  assert.doesNotMatch(ps1, /taskkill|\/IM python/i);
});
