# =============================================================================
#  AlkymIA · Turbo OS — désinstallation du cockpit (Windows, sans droits administrateur)
#
#  Double-cliquez sur DESINSTALLER-WINDOWS.bat.
#  Supprime : %LOCALAPPDATA%\Cockpit (Python embarqué, cockpit, configuration avec la clé,
#  journaux) et les raccourcis « AlkymIA Cockpit » (Démarrage et Bureau).
#  Ne touche à rien d'autre. L'historique conservé par le navigateur s'efface depuis le
#  cockpit (bouton « Effacer l'historique »).
# =============================================================================
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$Base = Join-Path $env:LOCALAPPDATA 'Cockpit'
$AppDir = Join-Path $Base 'app'
$PyDir = Join-Path $Base 'python'
$Nom = 'AlkymIA Cockpit'
$Raccourcis = @((Join-Path ([Environment]::GetFolderPath('Startup')) "$Nom.lnk"),
                (Join-Path ([Environment]::GetFolderPath('Desktop')) "$Nom.lnk"))

Write-Host '==> Arrêt du cockpit'
$motif = '*' + (Join-Path $AppDir 'serveur.py') + '*'
try {
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction Stop |
        Where-Object { $_.CommandLine -and $_.CommandLine -like $motif } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
} catch {
    Get-Process -Name pythonw, python -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path -like (Join-Path $PyDir '*') } |
        Stop-Process -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 1

Write-Host '==> Suppression des fichiers et des raccourcis'
foreach ($r in $Raccourcis) { if (Test-Path -LiteralPath $r) { Remove-Item -LiteralPath $r -Force } }
if (Test-Path -LiteralPath $Base) {
    for ($n = 0; $n -lt 5; $n++) {
        try { Remove-Item -LiteralPath $Base -Recurse -Force; break } catch { Start-Sleep -Seconds 1 }
    }
}

$reste = @($Raccourcis + $Base | Where-Object { Test-Path -LiteralPath $_ })
if ($reste.Count -eq 0) {
    Write-Host 'FINI - cockpit désinstallé'
    exit 0
}
foreach ($r in $reste) { Write-Host "    encore présent : $r" }
Write-Host 'FINI - désinstallation incomplète (voir ci-dessus)'
exit 1
