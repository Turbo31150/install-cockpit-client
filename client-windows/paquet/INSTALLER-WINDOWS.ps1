# =============================================================================
#  AlkymIA · Turbo OS — installation du cockpit (Windows, sans droits administrateur)
#
#  Double-cliquez sur INSTALLER-WINDOWS.bat, ou, dans une invite de commandes :
#      INSTALLER-WINDOWS.bat --cle VOTRE_CLE
#
#  Options :
#      --cle CLE          clé d'accès reçue : elle sert UNIQUEMENT à tester l'accès
#                         pendant l'installation. Elle n'est jamais enregistrée : le
#                         cockpit la redemande à chaque ouverture.
#      --passerelle URL   adresse de la passerelle (défaut : http://100.111.120.117:18900)
#      --port N           port local du cockpit (défaut : 8610)
#      --sans-ouvrir      ne pas ouvrir le navigateur à la fin
#
#  Installe, pour votre session uniquement, dans %LOCALAPPDATA%\Cockpit :
#      python\        Python officiel embarqué (empreinte vérifiée avant installation)
#      app\           le cockpit
#      config.json    adresse du service, sans clé (accès réservé à votre compte)
#      donnees.json   vos tâches, créneaux et notes (accès réservé à votre compte)
#      journaux\      journaux (jamais la clé)
#  + raccourci « AlkymIA Cockpit » dans le dossier Démarrage (lancement à l'ouverture de session)
#  + raccourci « AlkymIA Cockpit » sur le Bureau.
#  Jamais : droits administrateur, pare-feu, service Windows, modification de Tailscale.
#  Le cockpit écoute sur 127.0.0.1 seulement.
#  Dernière ligne affichée :  FINI - cockpit: OK|KO - passerelle: OK|KO
#  La clé est personnelle et valable 48 h : copiez-la en entier, sans espace, et ne la
#  recopiez jamais dans un compte rendu.
# =============================================================================
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$PYTHON_ZIP = 'python-3.12.10-embed-amd64.zip'
$PYTHON_SHA256 = '4ACBED6DD1C744B0376E3B1CF57CE906F9DC9E95E68824584C8099A63025A3C3'
$script:CockpitEtat = 'KO'
$script:PasserelleEtat = 'KO'

function Etape([string]$t) { Write-Host ''; Write-Host "==> $t" }
function Info([string]$t) { Write-Host "    $t" }
function Fini { Write-Host ("FINI - cockpit: {0} - passerelle: {1}" -f $script:CockpitEtat, $script:PasserelleEtat) }
function Echec([string]$t) {
    Write-Host ''
    Write-Host "[ERREUR] $t"
    Fini
    exit 1
}
function Cle-Valide([string]$c) { return ($null -ne $c) -and ($c -cmatch '^[\x21-\x7E]{32,256}$') }

# --- arguments ----------------------------------------------------------------------
$Cle = $null
$CleFournie = $false
$Passerelle = ''
$Port = '8610'
$Ouvrir = $true
$i = 0
while ($i -lt $args.Count) {
    $a = [string]$args[$i]
    if ($a -match '^(--cle|-cle)=(.*)$') { $Cle = $Matches[2]; $CleFournie = $true; $i += 1 }
    elseif ($a -match '^(--cle|-cle)$') {
        if ($i + 1 -ge $args.Count) { Echec '--cle attend une valeur.' }
        $Cle = [string]$args[$i + 1]; $CleFournie = $true; $i += 2
    }
    elseif ($a -match '^(--passerelle|-passerelle)=(.*)$') { $Passerelle = $Matches[2]; $i += 1 }
    elseif ($a -match '^(--passerelle|-passerelle)$') {
        if ($i + 1 -ge $args.Count) { Echec '--passerelle attend une adresse.' }
        $Passerelle = [string]$args[$i + 1]; $i += 2
    }
    elseif ($a -match '^(--port|-port)=(.*)$') { $Port = $Matches[2]; $i += 1 }
    elseif ($a -match '^(--port|-port)$') {
        if ($i + 1 -ge $args.Count) { Echec '--port attend un numéro.' }
        $Port = [string]$args[$i + 1]; $i += 2
    }
    elseif ($a -match '^(--sans-ouvrir|-sans-ouvrir)$') { $Ouvrir = $false; $i += 1 }
    elseif ($a -match '^(--sans-pause|-sans-pause)$') { $i += 1 }
    elseif ($a -match '^(-h|--help|/\?)$') { Get-Content -LiteralPath $PSCommandPath -TotalCount 28 | Select-Object -Skip 1; exit 0 }
    else { Echec "Option inconnue : $a" }
}

$Ici = Split-Path -Parent $PSCommandPath
$Base = Join-Path $env:LOCALAPPDATA 'Cockpit'
$PyDir = Join-Path $Base 'python'
$AppDir = Join-Path $Base 'app'
$Config = Join-Path $Base 'config.json'
$Journaux = Join-Path $Base 'journaux'
$Journal = Join-Path $Journaux 'client.log'
$Url = "http://127.0.0.1:$Port"
$Nom = 'AlkymIA Cockpit'

# --- 0. contrôles ----------------------------------------------------------------------
Etape 'AlkymIA · Turbo OS — contrôles'
$identite = [Security.Principal.WindowsIdentity]::GetCurrent()
if ((New-Object Security.Principal.WindowsPrincipal($identite)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Echec "Ne lancez PAS ce script en tant qu'administrateur : l'installation se fait dans votre session."
}
if (-not $env:LOCALAPPDATA) { Echec 'Dossier %LOCALAPPDATA% introuvable.' }
if ($env:PROCESSOR_ARCHITECTURE -eq 'x86' -and -not $env:PROCESSOR_ARCHITEW6432) {
    Echec 'Windows 64 bits requis (le Python embarqué est en 64 bits).'
}
foreach ($f in @("client\serveur.py", "client\web\index.html", "python\$PYTHON_ZIP")) {
    if (-not (Test-Path -LiteralPath (Join-Path $Ici $f))) { Echec "Fichier introuvable à côté du script : $f" }
}
if ($Port -notmatch '^\d{4,5}$' -or [int]$Port -lt 1024 -or [int]$Port -gt 65535) { Echec "Port invalide : $Port (1024 à 65535)." }
if ($Passerelle -and $Passerelle -notmatch '^https?://\S+$') { Echec "Adresse de passerelle invalide : $Passerelle (attendu http://adresse:port)." }
if ($CleFournie -and -not (Cle-Valide $Cle)) {
    Echec "Clé d'accès invalide : elle doit faire 32 à 256 caractères, sans espace. Copiez-la en entier."
}
$os = [Environment]::OSVersion.Version
Info ("Windows {0}.{1} (build {2}) · utilisateur {3}" -f $os.Major, $os.Minor, $os.Build, $env:USERNAME)

# --- 1. clé d'accès -------------------------------------------------------------------------
Etape "Clé d'accès"
Info "la clé est le secret d'ouverture du cockpit : elle n'est PAS enregistrée sur cet ordinateur."
if ($CleFournie) {
    Info "clé fournie : elle sert seulement à tester l'accès maintenant"
} elseif ([Environment]::UserInteractive -and -not [Console]::IsInputRedirected) {
    $s = Read-Host "    Collez votre cle pour tester l'acces tout de suite (Entree pour passer)" -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)
    try { $Cle = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
    $Cle = $Cle.Trim()
    if ($Cle) {
        if (-not (Cle-Valide $Cle)) { Echec "Clé d'accès invalide : 32 à 256 caractères, sans espace." }
        $CleFournie = $true
    }
}
if (-not $CleFournie) { Info 'aucune clé fournie : seule la santé du service sera testée' }
Info 'à chaque ouverture, le cockpit demande votre clé ; ne la recopiez jamais dans un compte rendu.'

# --- 2. arrêt d'une version précédente, contrôle du port ---------------------------------------
Etape "Arrêt d'une éventuelle version précédente"
function Arreter-Cockpit {
    $motif = '*' + (Join-Path $AppDir 'serveur.py') + '*'
    try {
        Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction Stop |
            Where-Object { $_.CommandLine -and $_.CommandLine -like $motif } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    } catch {
        Get-Process -Name pythonw, python -ErrorAction SilentlyContinue |
            Where-Object { $_.Path -and $_.Path -like ((Join-Path $PyDir '*')) } |
            Stop-Process -Force -ErrorAction SilentlyContinue
    }
}
function Port-Occupe {
    $c = $null
    try { $c = Get-NetTCPConnection -LocalPort ([int]$Port) -State Listen -ErrorAction Stop | Select-Object -First 1 } catch { $c = $null }
    if ($c) {
        $p = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
        if ($p) { return $p.ProcessName } else { return 'un autre programme' }
    }
    $t = New-Object Net.Sockets.TcpClient
    try { $t.Connect('127.0.0.1', [int]$Port); return 'un autre programme' } catch { return '' } finally { $t.Close() }
}
Arreter-Cockpit
$occupant = ''
for ($n = 0; $n -lt 10; $n++) {
    $occupant = Port-Occupe
    if (-not $occupant) { break }
    Start-Sleep -Seconds 1
}
if ($occupant) {
    Echec "Le port $Port est déjà utilisé par « $occupant ». Fermez ce programme ou relancez avec : INSTALLER-WINDOWS.bat --port 8620"
}
Info "port $Port libre"

# --- 3. Python embarqué (vérifié) et cockpit ------------------------------------------------------
Etape "Installation dans $Base"
$zip = Join-Path (Join-Path $Ici 'python') $PYTHON_ZIP
$empreinte = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash
if ($empreinte -ne $PYTHON_SHA256) { Echec "Python embarqué altéré (empreinte $empreinte inattendue) : téléchargez à nouveau le paquet." }
Info 'Python embarqué : empreinte SHA-256 conforme'
foreach ($d in @($Base, $Journaux)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
foreach ($d in @($PyDir, $AppDir)) { if (Test-Path -LiteralPath $d) { Remove-Item -LiteralPath $d -Recurse -Force } }
Expand-Archive -LiteralPath $zip -DestinationPath $PyDir -Force
Copy-Item -LiteralPath (Join-Path $Ici 'client') -Destination $AppDir -Recurse -Force
Get-ChildItem -LiteralPath $Base -Recurse -File | Unblock-File -ErrorAction SilentlyContinue
$Py = Join-Path $PyDir 'python.exe'
$Pyw = Join-Path $PyDir 'pythonw.exe'
$version = & $Py -I -c "import platform; print(platform.python_version())"
if ($LASTEXITCODE -ne 0) { Echec 'Le Python embarqué ne démarre pas.' }
Info "Python $version"

$argsConfig = @('-I', (Join-Path $AppDir 'serveur.py'), '--ecrire-config', '--config', $Config)
if ($Passerelle) { $argsConfig += @('--passerelle', $Passerelle) }
& $Py @argsConfig
if ($LASTEXITCODE -ne 0) { Echec 'Configuration non enregistrée (voir ci-dessus).' }
$sid = $identite.User.Value
& icacls.exe $Config /inheritance:r /grant:r "*${sid}:(F)" | Out-Null
if ($LASTEXITCODE -ne 0) { Info 'AVERTISSEMENT : droits du fichier de configuration non restreints.' }

# --- 4. raccourcis (Démarrage + Bureau) ------------------------------------------------------------
Etape 'Raccourcis'
function Q([string]$t) { return '"' + $t + '"' }
$argsServeur = '-I ' + (Q (Join-Path $AppDir 'serveur.py')) + " --port $Port --config " + (Q $Config) + ' --journal ' + (Q $Journal)
$shell = New-Object -ComObject WScript.Shell
$demarrage = [Environment]::GetFolderPath('Startup')
$lnk = $shell.CreateShortcut((Join-Path $demarrage "$Nom.lnk"))
$lnk.TargetPath = $Pyw
$lnk.Arguments = $argsServeur
$lnk.WorkingDirectory = $AppDir
$lnk.WindowStyle = 7
$lnk.Description = 'Démarre le cockpit local (127.0.0.1)'
$lnk.Save()
Info "démarrage automatique : $demarrage\$Nom.lnk"
$bureau = [Environment]::GetFolderPath('Desktop')
$lnk = $shell.CreateShortcut((Join-Path $bureau "$Nom.lnk"))
$lnk.TargetPath = $Pyw
$lnk.Arguments = $argsServeur + ' --lanceur'
$lnk.WorkingDirectory = $AppDir
$lnk.Description = 'Ouvre le cockpit (http://127.0.0.1)'
$lnk.Save()
Info "bureau : $bureau\$Nom.lnk"

# --- 5. démarrage et tests ----------------------------------------------------------------------------
Etape "Démarrage et test du cockpit ($Url)"
Start-Process -FilePath $Pyw -ArgumentList $argsServeur -WorkingDirectory $AppDir -WindowStyle Hidden
$sonde = 1
for ($n = 0; $n -lt 30; $n++) {
    Start-Sleep -Seconds 1
    & $Py -I (Join-Path $AppDir 'serveur.py') --sonder --port $Port | Out-Null
    $sonde = $LASTEXITCODE
    if ($sonde -ne 1) { break }
}
if ($sonde -ne 1) {
    $script:CockpitEtat = 'OK'
    Info '/api/etat : 200'
    try {
        $adresses = @(Get-NetTCPConnection -LocalPort ([int]$Port) -State Listen -ErrorAction Stop | ForEach-Object { $_.LocalAddress } | Sort-Object -Unique)
        Info ('écoute : ' + ($adresses -join ' '))
        if ($adresses.Count -ne 1 -or $adresses[0] -ne '127.0.0.1') {
            $script:CockpitEtat = 'KO'
            Info "ATTENTION : écoute inattendue (attendu 127.0.0.1:$Port uniquement)"
        }
    } catch { }
} else {
    Info 'le cockpit ne répond pas — dernières lignes du journal :'
    foreach ($f in @($Journal, "$Journal.err")) {
        if (Test-Path -LiteralPath $f) { Get-Content -LiteralPath $f -Tail 15 | ForEach-Object { Info "  $_" } }
    }
}

Etape 'Test de la passerelle'
$entree = ''
if ($Cle) { $entree = $Cle }
$entree | & $Py -I (Join-Path $AppDir 'serveur.py') --verifier --config $Config
$rc = $LASTEXITCODE
$Cle = $null; $entree = $null
if ($rc -eq 0) {
    $script:PasserelleEtat = 'OK'
    if ($script:CockpitEtat -eq 'OK') {
        $ok = $false
        for ($n = 0; $n -lt 4; $n++) {
            Start-Sleep -Seconds 3
            & $Py -I (Join-Path $AppDir 'serveur.py') --sonder --port $Port
            if ($LASTEXITCODE -eq 0) { $ok = $true; break }
        }
        if (-not $ok) {
            $script:PasserelleEtat = 'KO'
            Info "le test direct joint la passerelle, mais PAS le cockpit lancé en arrière-plan :"
            Info "vérifiez que le pare-feu ou l'antivirus n'empêche pas pythonw.exe d'accéder au réseau."
        }
    }
} else {
    switch ($rc) {
        13 { Info '=> Accès expiré : une nouvelle clé doit vous être fournie.' }
        14 { Info '=> Clé refusée.' }
        21 { Info '=> Clé mal copiée (format inattendu).' }
        15 { Info '=> Adhésion pas encore approuvée (ou accès suspendu).' }
        11 { Info '=> Passerelle injoignable (voir la cause ci-dessus).' }
        default { Info "=> Passerelle non vérifiée (code $rc)." }
    }
}

if ($script:CockpitEtat -eq 'OK' -and $Ouvrir) { Start-Process "$Url/" }

Write-Host ''
Write-Host "Cockpit      : $Url"
Write-Host "Raccourci    : Bureau > $Nom"
Write-Host "Journaux     : $Journaux"
Write-Host "Désinstaller : $Ici\DESINSTALLER-WINDOWS.bat"
Fini
if ($script:CockpitEtat -ne 'OK') { exit 1 }
if ($script:PasserelleEtat -ne 'OK') { exit 2 }
exit 0
