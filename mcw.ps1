<#
.SYNOPSIS
  Minecraft x The Witcher 3: check, install, start and stop everything with one script.

.DESCRIPTION
  .\mcw.ps1 doctor      what is installed, what is missing and how to get it
  .\mcw.ps1 install     build the Minecraft mods, install the Witcher 3 mods. With REDkit: Minecraft blocks and the
                        Steve/horse models are built from YOUR Minecraft jar. Without REDkit (or -Lite): lite mode,
                        blocks are shown as Witcher crates and there is no Steve body or horse model.
  .\mcw.ps1 start       launch Witcher 3 with the debug channel, the Minecraft server, the hidden client and the bridge
  .\mcw.ps1 stop        stop the bridge, client and server (the Minecraft world is saved); Witcher 3 stays open
  .\mcw.ps1 status      what is running
  .\mcw.ps1 uninstall   remove the mods from the Witcher 3 folder
  .\mcw.ps1 paths       where the game, REDkit, Minecraft jar and JDK were found

  Paths are found automatically (Steam, GOG, Minecraft launcher runtime); override with the environment variables
  MCW_W3_DIR, MCW_REDKIT_DIR, MCW_MC_JAR, MCW_JAVA_HOME, MCW_PYTHON. Messages are Russian on a Russian Windows,
  English otherwise (MCW_LANG=en|ru to force). Logs of the build steps: %LOCALAPPDATA%\mcw\logs.

.EXAMPLE
  .\mcw.ps1 install
  .\mcw.ps1 install -Lite
  .\mcw.ps1 start
  .\mcw.ps1 stop
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('help', 'doctor', 'install', 'start', 'stop', 'status', 'uninstall', 'paths')]
    [string]$Command = 'help',
    [switch]$Lite,          # install: lite mode even when REDkit is installed
    [string]$Skin = '',     # install: your own 64x64 Minecraft skin (classic/wide arms) for the Steve body
    [switch]$AcceptEula,    # install: accept the Minecraft EULA (https://aka.ms/MinecraftEULA) without asking
    [switch]$NoBackup,      # install: do not back up Documents\The Witcher 3 (settings and saves)
    [switch]$NoGame,        # start: do not launch Witcher 3 (it is already running or you start it yourself)
    [switch]$ForceScripts,  # start: launch Witcher 3 with -forcescriptcompilation
    [switch]$CloseGame,     # stop: also close Witcher 3 (unsaved progress is lost)
    [switch]$Purge          # uninstall: also delete build caches in %LOCALAPPDATA%\mcw (backups are kept)
)
$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Data = Join-Path $env:LOCALAPPDATA 'mcw'
$LogDir = Join-Path $Data 'logs'
$StateFile = Join-Path $Data 'state.json'
$W3AppId = '292030'
$FabricInstaller = '1.1.2'      # Fabric server launcher: meta.fabricmc.net/v2/versions/loader/<mc>/<loader>/<installer>/server/jar
$Ru = if ($env:MCW_LANG) { $env:MCW_LANG -like 'ru*' } else { (Get-UICulture).Name -like 'ru*' }

function T([string]$En, [string]$RuText) { if ($script:Ru) { $RuText } else { $En } }
function Say([string]$En, [string]$RuText, [string]$Color = '') {
    if ($Color) { Write-Host (T $En $RuText) -ForegroundColor $Color } else { Write-Host (T $En $RuText) }
}
function Step([string]$En, [string]$RuText) { Write-Host ''; Write-Host ('== ' + (T $En $RuText)) -ForegroundColor Cyan }
function Fail([string]$En, [string]$RuText) { throw (T $En $RuText) }

# Native command with stderr dropped. In Windows PowerShell 5.1 "2>$null" under ErrorActionPreference=Stop turns the
# first stderr line into a terminating error, so the preference is relaxed for the call.
function Invoke-Quiet([scriptblock]$Block) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'SilentlyContinue'
    try { & $Block 2>$null } finally { $ErrorActionPreference = $old }
}

function Quote-Arg([string]$a) {
    if ($a -eq '' -or $a -match '[\s"&|<>^()]') { return '"' + ($a -replace '"', '""') + '"' }
    return $a
}

# ---------------------------------------------------------------------------------------------- environment
function Find-Python {
    if ($env:MCW_PYTHON -and (Test-Path -LiteralPath $env:MCW_PYTHON)) { return $env:MCW_PYTHON }
    foreach ($c in @(@('py', '-3.12'), @('python'), @('py', '-3'), @('python3'))) {
        if (-not (Get-Command $c[0] -ErrorAction SilentlyContinue)) { continue }
        $rest = @($c | Select-Object -Skip 1)
        try {
            $out = @(Invoke-Quiet { & $c[0] @rest -c 'import sys; print(sys.version_info[0] * 100 + sys.version_info[1]); print(sys.executable)' })
        } catch { continue }
        if ($LASTEXITCODE -ne 0 -or $out.Count -lt 2) { continue }   # python.exe из Microsoft Store — заглушка
        if ([int]$out[0] -ge 311) { return $out[1].Trim() }
    }
    return $null
}

function Get-McwPaths([string]$Py) {
    $json = & $Py (Join-Path $Root 'spike\w3paths.py') | Out-String
    if ($LASTEXITCODE -ne 0) { Fail 'spike\w3paths.py failed' 'spike\w3paths.py упал' }
    return $json | ConvertFrom-Json
}

function Get-State {
    if (Test-Path -LiteralPath $StateFile) {
        try { return Get-Content -LiteralPath $StateFile -Raw -Encoding UTF8 | ConvertFrom-Json } catch {}
    }
    return [pscustomobject]@{ scripts = ''; force = $false; mode = '' }
}

function Save-State($s) {
    New-Item -ItemType Directory -Force $Data | Out-Null
    [IO.File]::WriteAllText($StateFile, ($s | ConvertTo-Json), (New-Object Text.UTF8Encoding $false))
}

function Get-ScriptsHash([string]$Dir) {
    if (-not (Test-Path -LiteralPath $Dir)) { return '' }
    $files = @(Get-ChildItem -LiteralPath $Dir -Recurse -File -Filter '*.ws' | Sort-Object Name)
    $sha = [Security.Cryptography.SHA256]::Create()
    $all = ($files | ForEach-Object { $_.Name + ':' + (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash }) -join "`n"
    return ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($all))) -replace '-', '').Substring(0, 16)
}

function Read-GradleProp([string]$Name) {
    $line = Get-Content -LiteralPath (Join-Path $Root 'mc\gradle.properties') | Where-Object { $_ -match "^$Name=" } | Select-Object -First 1
    return ($line -split '=', 2)[1].Trim()
}

# Run a tool, its output goes to a log file; on failure print the tail and stop.
function Invoke-Logged([string]$Name, [string]$WorkDir, [string]$File, [string[]]$Arguments = @()) {
    New-Item -ItemType Directory -Force $LogDir | Out-Null
    $log = Join-Path $LogDir ('{0}-{1}.log' -f (Get-Date -Format 'yyyyMMdd-HHmmss'), $Name)
    $line = (@($File) + $Arguments | ForEach-Object { Quote-Arg $_ }) -join ' '
    $psi = New-Object Diagnostics.ProcessStartInfo
    $psi.FileName = $env:ComSpec
    $psi.Arguments = '/d /s /c "' + $line + ' > ' + (Quote-Arg $log) + ' 2>&1"'
    $psi.WorkingDirectory = $WorkDir
    $psi.UseShellExecute = $false
    $p = [Diagnostics.Process]::Start($psi)
    $p.WaitForExit()
    if ($p.ExitCode -ne 0) {
        Say "   failed (exit code $($p.ExitCode)), last lines of $log :" "   ошибка (код $($p.ExitCode)), конец журнала $log :" 'Red'
        Get-Content -LiteralPath $log -Tail 25 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "   | $_" }
        Fail "$Name failed, see $log" "${Name}: ошибка, см. $log"
    }
    Write-Host ('   ' + (T 'ok, log: ' 'готово, журнал: ') + $log) -ForegroundColor DarkGray
}

# ---------------------------------------------------------------------------------------------- processes
function Get-WitcherProcess { @(Get-CimInstance Win32_Process -Filter "Name='witcher3.exe'") | Select-Object -First 1 }

function Get-ServerProcess {
    $pidFile = Join-Path $Root 'mc\run\server.pid'
    if (-not (Test-Path -LiteralPath $pidFile)) { return $null }
    $id = [int](Get-Content -LiteralPath $pidFile -TotalCount 1)
    $p = Get-Process -Id $id -ErrorAction SilentlyContinue
    if ($null -ne $p -and $p.ProcessName -like 'java*') { return $p }
    return $null
}

function Get-ClientProcess {
    $cfg = [regex]::Escape((Join-Path $Root 'mcclient\.gradle\loom-cache\launch.cfg'))
    @(Get-CimInstance Win32_Process -Filter "Name='java.exe'" |
        Where-Object { $_.CommandLine -match "fabric\.dli\.config=$cfg" -and $_.CommandLine -match 'fabric\.dli\.env=client' }) |
        Select-Object -First 1
}

function Get-BridgeProcess {
    $script = [regex]::Escape((Join-Path $Root 'bridge\bridge.py'))
    @(Get-CimInstance Win32_Process -Filter "Name like 'python%'" | Where-Object { $_.CommandLine -match $script }) |
        Select-Object -First 1
}

# ---------------------------------------------------------------------------------------------- doctor
function Get-Checks {
    $r = New-Object System.Collections.Generic.List[object]
    function Add([string]$Item, [string]$Status, [string]$Detail) { $r.Add([pscustomobject]@{ Item = $Item; Status = $Status; Detail = $Detail }) }

    $py = Find-Python
    if ($py) { Add 'Python 3.11+' 'ok' $py }
    else { Add 'Python 3.11+' 'fail' (T 'not found: install Python 3.12 from python.org (tick "Add python.exe to PATH")' 'не найден: поставь Python 3.12 с python.org (галка «Add python.exe to PATH»)') }
    $paths = $null
    if ($py) { $paths = Get-McwPaths $py }

    if ($paths -and $paths.game) {
        $exe = Join-Path $paths.game 'bin\x64_dx12\witcher3.exe'
        $ver = ((Get-Item -LiteralPath $exe).VersionInfo.FileVersion -replace '[^\d.].*$', '')
        $st = if ($ver -like '5.*') { 'ok' } else { 'warn' }
        Add 'Witcher 3' $st ("{0} ({1}, {2}){3}" -f $paths.game, $paths.store, $ver, $(if ($st -ne 'ok') { T ' - tested on 5.00c (5.0.0.1044392) only' ' — проверено только на 5.00c (5.0.0.1044392)' } else { '' }))
        $mods = Join-Path $paths.game 'mods'
        $src = Get-ScriptsHash (Join-Path $Root 'spike\mod\modMCW')
        $dst = Get-ScriptsHash (Join-Path $mods 'modMCW')
        if (-not $dst) { Add (T 'Witcher scripts (modMCW)' 'Скрипты Ведьмака (modMCW)') 'warn' (T 'not installed: .\mcw.ps1 install' 'не установлены: .\mcw.ps1 install') }
        elseif ($dst -ne $src) { Add (T 'Witcher scripts (modMCW)' 'Скрипты Ведьмака (modMCW)') 'warn' (T 'outdated: .\mcw.ps1 install' 'устарели: .\mcw.ps1 install') }
        else { Add (T 'Witcher scripts (modMCW)' 'Скрипты Ведьмака (modMCW)') 'ok' (Join-Path $mods 'modMCW') }
        $full = (Test-Path -LiteralPath (Join-Path $mods 'modMCWBlocks\content\blob0.bundle')) -and (Test-Path -LiteralPath (Join-Path $mods 'modMCWModels\content\blob0.bundle'))
        if ($full) { Add (T 'Blocks and models' 'Блоки и модели') 'ok' 'modMCWBlocks, modMCWModels' }
        else { Add (T 'Blocks and models' 'Блоки и модели') 'warn' (T 'not built: lite mode (crates instead of blocks, no Steve body); needs REDkit' 'не собраны: лайт-режим (ящики вместо блоков, без тела Стива); нужен REDkit') }
    } else {
        Add 'Witcher 3' 'fail' (T 'not found (Steam/GOG): set MCW_W3_DIR to the game folder' 'не найден (Steam/GOG): задай MCW_W3_DIR = папка игры')
    }

    if ($paths -and $paths.java) { Add 'JDK 21+' 'ok' $paths.java }
    else { Add 'JDK 21+' 'fail' (T 'not found: install Temurin 21 (adoptium.net) or run Minecraft 1.21.11 once in the official launcher; or set MCW_JAVA_HOME' 'не найден: поставь Temurin 21 (adoptium.net) или запусти Minecraft 1.21.11 в официальном лаунчере; или задай MCW_JAVA_HOME') }

    if ($paths -and $paths.mc_jar) { Add 'Minecraft 1.21.11' 'ok' $paths.mc_jar }
    else { Add 'Minecraft 1.21.11' 'warn' (T 'client jar not found: start Minecraft Java 1.21.11 once in the official launcher (needed to build blocks and models), or set MCW_MC_JAR' 'jar клиента не найден: запусти Minecraft Java 1.21.11 один раз в официальном лаунчере (нужен для блоков и моделей) или задай MCW_MC_JAR') }

    if ($paths -and $paths.redkit) { Add 'REDkit' 'ok' $paths.redkit }
    else { Add 'REDkit' 'warn' (T 'not installed (optional, ~110 GB; free for Witcher 3 owners on Steam/GOG): without it - lite mode' 'не установлен (необязателен, ~110 ГБ; бесплатен владельцам Ведьмака в Steam/GOG): без него — лайт-режим') }

    if ($py -and $paths -and $paths.redkit) {
        Invoke-Quiet { & $py -c 'import PIL' } | Out-Null
        if ($LASTEXITCODE -eq 0) { Add 'Pillow' 'ok' '' } else { Add 'Pillow' 'warn' (T 'not installed: install does pip install -r tools\requirements.txt' 'не установлен: install сделает pip install -r tools\requirements.txt') }
    }

    $overlay = Join-Path $Root 'overlay\mcw_overlay.exe'
    if (Test-Path -LiteralPath $overlay) { Add (T 'Overlay' 'Оверлей') 'ok' $overlay }
    else { Add (T 'Overlay' 'Оверлей') 'warn' (T 'overlay\mcw_overlay.exe missing: install builds it with Visual Studio 2022 (C++), or take it from the GitHub release' 'нет overlay\mcw_overlay.exe: install соберёт его Visual Studio 2022 (C++), или возьми из релиза на GitHub') }

    $run = Join-Path $Root 'mc\run'
    $eula = (Test-Path -LiteralPath (Join-Path $run 'eula.txt')) -and (Select-String -LiteralPath (Join-Path $run 'eula.txt') -Pattern '^eula=true' -Quiet)
    $jars = @(Get-ChildItem -LiteralPath (Join-Path $run 'mods') -Filter 'mcw-*.jar' -ErrorAction SilentlyContinue).Count -gt 0
    $launcher = Test-Path -LiteralPath (Join-Path $run 'fabric-server-launch.jar')
    if ($eula -and $jars -and $launcher) { Add (T 'Minecraft server' 'Сервер Minecraft') 'ok' $run }
    else { Add (T 'Minecraft server' 'Сервер Minecraft') 'warn' (T 'not prepared: .\mcw.ps1 install' 'не подготовлен: .\mcw.ps1 install') }
    return [pscustomobject]@{ Checks = $r; Py = $py; Paths = $paths }
}

function Show-Checks($res) {
    foreach ($c in $res.Checks) {
        $tag, $color = switch ($c.Status) { 'ok' { '[ ok ]', 'Green' } 'warn' { '[warn]', 'Yellow' } default { '[FAIL]', 'Red' } }
        Write-Host ('{0} {1,-28} ' -f $tag, $c.Item) -ForegroundColor $color -NoNewline
        Write-Host $c.Detail
    }
}

function Invoke-Doctor {
    $res = Get-Checks
    Show-Checks $res
    if (@($res.Checks | Where-Object Status -eq 'fail').Count -gt 0) {
        Say "`nSome requirements are missing (FAIL) - see README, Requirements." "`nНе хватает обязательного (FAIL) — см. README, «Что нужно»." 'Red'
        return $false
    }
    return $true
}

# ---------------------------------------------------------------------------------------------- install
$LiteBegin = '# mcw-lite-begin (written by mcw.ps1 install: no REDkit mods -> Witcher crates, no Steve body)'
$LiteEnd = '# mcw-lite-end'

function Set-LiteConfig([bool]$On) {
    $f = Join-Path $Root 'bridge\bridge.local.toml'
    $text = if (Test-Path -LiteralPath $f) { [IO.File]::ReadAllText($f) } else { '' }
    $text = [regex]::Replace($text, '(?s)' + [regex]::Escape($LiteBegin) + '.*?' + [regex]::Escape($LiteEnd) + '\r?\n?', '')
    if ($On) {
        $text = $text.TrimEnd() + $(if ($text.Trim()) { "`n`n" } else { '' }) + $LiteBegin + "`n[display]`ndefault_template = 0`n`n[display.templates]`n`n[skin]`nenabled = false`n" + $LiteEnd + "`n"
    }
    if ($text.Trim()) { [IO.File]::WriteAllText($f, $text, (New-Object Text.UTF8Encoding $false)) }
    elseif (Test-Path -LiteralPath $f) { Remove-Item -LiteralPath $f }
}

function Copy-GameMod([string]$Src, [string]$Mods, [string]$Name) {
    if ($Name -notmatch '^modMCW(Blocks|Models)$') { Fail "refusing to touch $Name" "отказ: $Name" }
    if (-not (Test-Path -LiteralPath (Join-Path $Src 'content\blob0.bundle'))) { Fail "$Src has no content\blob0.bundle" "в $Src нет content\blob0.bundle" }
    $dst = Join-Path $Mods $Name
    if (Test-Path -LiteralPath $dst) { Remove-Item -LiteralPath $dst -Recurse -Force }
    Copy-Item -LiteralPath $Src -Destination $dst -Recurse
    Write-Host "   $Src -> $dst" -ForegroundColor DarkGray
}

function Invoke-Install {
    Step 'Checking requirements' 'Проверка требований'
    $res = Get-Checks
    Show-Checks $res
    if (@($res.Checks | Where-Object Status -eq 'fail').Count -gt 0) { Fail 'Missing requirements (FAIL above), see README.' 'Не хватает обязательного (FAIL выше), см. README.' }
    $py, $paths = $res.Py, $res.Paths
    $env:MCW_PYTHON = $py
    $env:JAVA_HOME = $paths.java
    $env:MCW_JAVA_HOME = $paths.java
    $full = [bool]$paths.redkit -and -not $Lite
    if ($full -and -not $paths.mc_jar) {
        Fail 'REDkit is installed, but the Minecraft 1.21.11 client jar is not found: start Minecraft Java 1.21.11 once in the official launcher (or use -Lite).' 'REDkit есть, но jar клиента Minecraft 1.21.11 не найден: запусти Minecraft Java 1.21.11 один раз в официальном лаунчере (или -Lite).'
    }
    if ((Get-ServerProcess) -or (Get-ClientProcess) -or (Get-BridgeProcess)) { Fail 'Stop it first: .\mcw.ps1 stop' 'Сначала останови: .\mcw.ps1 stop' }

    $backups = Join-Path $Data 'backups'
    $userDir = Join-Path $paths.documents 'The Witcher 3'
    if (-not $NoBackup -and -not (Test-Path -LiteralPath $backups) -and (Test-Path -LiteralPath $userDir)) {
        Step 'Backing up Documents\The Witcher 3 (settings and saves)' 'Бэкап «Документы\The Witcher 3» (настройки и сейвы)'
        & (Join-Path $Root 'spike\backup_userdata.ps1') -Source $userDir -Destination $backups | Select-Object -Last 1
    }

    Step 'Minecraft server (Fabric 1.21.11)' 'Сервер Minecraft (Fabric 1.21.11)'
    $run = Join-Path $Root 'mc\run'
    New-Item -ItemType Directory -Force $run | Out-Null
    $eula = Join-Path $run 'eula.txt'
    if (-not ((Test-Path -LiteralPath $eula) -and (Select-String -LiteralPath $eula -Pattern '^eula=true' -Quiet))) {
        Say '   The local Minecraft server needs the Minecraft EULA: https://aka.ms/MinecraftEULA' '   Локальному серверу Minecraft нужно согласие с EULA Minecraft: https://aka.ms/MinecraftEULA'
        if (-not $AcceptEula) {
            $a = Read-Host (T '   Do you accept it? (yes/no)' '   Принимаешь? (да/нет)')
            if ($a -notmatch '^\s*(y|yes|д|да)\s*$') { Fail 'EULA not accepted, stopping.' 'EULA не принят, стоп.' }
        }
        [IO.File]::WriteAllText($eula, "# accepted by the user via mcw.ps1 install, $(Get-Date -Format s)`neula=true`n", (New-Object Text.UTF8Encoding $false))
    }
    $launcher = Join-Path $run 'fabric-server-launch.jar'
    if (-not (Test-Path -LiteralPath $launcher)) {
        $url = 'https://meta.fabricmc.net/v2/versions/loader/{0}/{1}/{2}/server/jar' -f (Read-GradleProp 'minecraft_version'), (Read-GradleProp 'loader_version'), $FabricInstaller
        Say "   downloading $url" "   качаю $url"
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        $ProgressPreference = 'SilentlyContinue'
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $launcher
    }
    Say '   building the server mod (first time: Gradle downloads Minecraft, a few minutes)' '   собираю серверный мод (в первый раз Gradle качает Minecraft, несколько минут)'
    Invoke-Logged 'gradle-server' (Join-Path $Root 'mc') (Join-Path $Root 'mc\gradlew.bat') @('build', 'deployRun', '--console=plain')

    Step 'Minecraft client (hidden, Fabric 1.21.11)' 'Клиент Minecraft (скрытый, Fabric 1.21.11)'
    Say '   building the client mod and downloading game assets (first time: several minutes)' '   собираю мод клиента и качаю ассеты игры (в первый раз — несколько минут)'
    Invoke-Logged 'gradle-client' (Join-Path $Root 'mcclient') (Join-Path $Root 'mcclient\gradlew.bat') @('build', 'downloadAssets', '--console=plain')

    Step 'Overlay' 'Оверлей'
    $overlay = Join-Path $Root 'overlay\mcw_overlay.exe'
    if (Test-Path -LiteralPath $overlay) { Say "   $overlay" "   $overlay" }
    elseif (Test-Path -LiteralPath (Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe')) {
        Invoke-Logged 'overlay' (Join-Path $Root 'overlay') (Join-Path $Root 'overlay\build.bat')
    } else {
        Say '   overlay\mcw_overlay.exe is missing and Visual Studio is not installed: download mcw_overlay.exe from the GitHub release into overlay\ (without it the Minecraft hand and HUD are not shown)' '   нет overlay\mcw_overlay.exe и нет Visual Studio: скачай mcw_overlay.exe из релиза на GitHub в overlay\ (без него не видно руки и HUD Minecraft)' 'Yellow'
    }

    Step 'Witcher 3 script mod (modMCW)' 'Скриптовый мод Ведьмака (modMCW)'
    $state = Get-State
    & (Join-Path $Root 'spike\install_mod.ps1') -GamePath $paths.game | Where-Object { $_ -notmatch '^\s' } | ForEach-Object { Write-Host "   $_" -ForegroundColor DarkGray }
    $hash = Get-ScriptsHash (Join-Path $Root 'spike\mod\modMCW')
    $force = [bool]$state.force -or ($hash -ne $state.scripts)

    $mods = Join-Path $paths.game 'mods'
    if ($full) {
        Step 'Blocks and models from your Minecraft jar (REDkit wcc_lite, ~10 min)' 'Блоки и модели из твоего jar Minecraft (REDkit wcc_lite, ~10 мин)'
        Invoke-Quiet { & $py -c 'import PIL' } | Out-Null
        if ($LASTEXITCODE -ne 0) { Invoke-Logged 'pip' $Root $py @('-m', 'pip', 'install', '-r', (Join-Path $Root 'tools\requirements.txt')) }
        $tools = Join-Path $Root 'tools'
        Say '   textures' '   текстуры'
        Invoke-Logged 'textures' $tools $py @('mc_textures.py', '--jar', $paths.mc_jar)
        Say '   blocks (modMCWBlocks, ~4 min)' '   блоки (modMCWBlocks, ~4 мин)'
        Invoke-Logged 'blocks' $tools $py @('build_blocks.py', 'all', '--allow-wcc')
        $margs = @('build_models.py', 'all', '--allow-wcc', '--jar', $paths.mc_jar)
        if ($Skin) { $margs += @('--skin', (Resolve-Path -LiteralPath $Skin).Path) }
        Say '   models (modMCWModels: Steve body, horse, ~4 min)' '   модели (modMCWModels: тело Стива, лошадь, ~4 мин)'
        Invoke-Logged 'models' $tools $py $margs
        Copy-GameMod (Join-Path $Data 'redkit_work\out\modMCWBlocks') $mods 'modMCWBlocks'
        Copy-GameMod (Join-Path $Data 'redkit_models\out\modMCWModels') $mods 'modMCWModels'
        Set-LiteConfig $false
        $force = $true          # новые бандлы игра читает только при старте
    } else {
        Step 'Lite mode (no REDkit)' 'Лайт-режим (без REDkit)'
        Say '   Minecraft blocks will be shown as Witcher crates; no Steve body or Minecraft horse model. Install REDkit and run install again for the full version.' '   Блоки Minecraft будут ящиками Ведьмака, без тела Стива и модели лошади. Полная версия — поставить REDkit и снова install.'
        Set-LiteConfig $true
    }
    Save-State ([pscustomobject]@{ scripts = $hash; force = $force; mode = $(if ($full) { 'full' } else { 'lite' }) })

    Step 'Done' 'Готово'
    Say 'Next: .\mcw.ps1 start   (or double-click mcw.cmd)' 'Дальше: .\mcw.ps1 start   (или двойной клик по mcw.cmd)' 'Green'
}

# ---------------------------------------------------------------------------------------------- start / stop
function Invoke-Start {
    $py = Find-Python
    if (-not $py) { Fail 'Python 3.11+ not found, run .\mcw.ps1 doctor' 'Python 3.11+ не найден, запусти .\mcw.ps1 doctor' }
    $paths = Get-McwPaths $py
    if (-not $paths.game -or -not $paths.java) { Fail 'Witcher 3 or JDK not found, run .\mcw.ps1 doctor' 'Не найден Ведьмак или JDK, запусти .\mcw.ps1 doctor' }
    if (-not (Test-Path -LiteralPath (Join-Path $paths.game 'mods\modMCW')) -or
        -not (Test-Path -LiteralPath (Join-Path $Root 'mc\run\fabric-server-launch.jar'))) {
        Fail 'Not installed yet: .\mcw.ps1 install' 'Ещё не установлено: .\mcw.ps1 install'
    }
    $env:MCW_PYTHON = $py
    $env:MCW_JAVA_HOME = $paths.java
    $env:MCW_W3_DIR = $paths.game
    $state = Get-State

    Step 'Witcher 3' 'Ведьмак 3'
    $w = Get-WitcherProcess
    if ($w) {
        if ($w.CommandLine -and $w.CommandLine -notmatch '(^|\s)-net(\s|$)') {
            Say '   Witcher 3 is running WITHOUT the debug channel (-net): quit it and run .\mcw.ps1 start again' '   Ведьмак запущен БЕЗ отладочного канала (-net): выйди из игры и снова .\mcw.ps1 start' 'Yellow'
        } else { Say "   already running (pid $($w.ProcessId))" "   уже запущен (pid $($w.ProcessId))" }
    } elseif ($NoGame) {
        Say '   not launched (-NoGame): start it with -net -debugscripts yourself' '   не запускаю (-NoGame): запусти его сам с -net -debugscripts'
    } else {
        $flags = @('-net', '-debugscripts')
        $force = $ForceScripts -or [bool]$state.force
        if ($force) { $flags += '-forcescriptcompilation' }
        if ($paths.store -eq 'steam' -and $paths.steam) {
            Start-Process -FilePath (Join-Path $paths.steam 'steam.exe') -ArgumentList (@('-applaunch', $W3AppId, '--launcher-skip') + $flags)
            Say "   launched via Steam ($($flags -join ' '))" "   запущен через Steam ($($flags -join ' '))"
            Say '   if Steam asks to allow launch options - allow' '   если Steam спросит про параметры запуска — разреши' 'DarkGray'
        } else {
            $exe = Join-Path $paths.game 'bin\x64_dx12\witcher3.exe'
            Start-Process -FilePath $exe -ArgumentList $flags -WorkingDirectory (Split-Path $exe)
            Say "   launched $exe ($($flags -join ' '))" "   запущен $exe ($($flags -join ' '))"
        }
        if ($force) { $state.force = $false; Save-State $state }
    }

    Step 'Minecraft server' 'Сервер Minecraft'
    if (Get-ServerProcess) { Say '   already running' '   уже запущен' }
    else { & (Join-Path $Root 'mc\run_server.ps1') | ForEach-Object { Write-Host "   $_" -ForegroundColor DarkGray } }

    Step 'Minecraft client (hidden window, muted)' 'Клиент Minecraft (скрытое окно, без звука)'
    if (Get-ClientProcess) { Say '   already running' '   уже запущен' }
    else { & (Join-Path $Root 'mcclient\run_client.ps1') | ForEach-Object { Write-Host "   $_" -ForegroundColor DarkGray } }

    Step 'Bridge and overlay' 'Мост и оверлей'
    $b = Get-BridgeProcess
    if ($b) { Say "   already running (pid $($b.ProcessId))" "   уже запущен (pid $($b.ProcessId))" }
    else {
        $bridge = Join-Path $Root 'bridge'
        $p = Start-Process -FilePath $py -ArgumentList (Quote-Arg (Join-Path $bridge 'bridge.py')) -WorkingDirectory $bridge -WindowStyle Hidden -PassThru
        Start-Sleep -Seconds 3
        if ($p.HasExited) { Fail "the bridge exited right away (code $($p.ExitCode)), see bridge\logs\bridge.log" "мост сразу вышел (код $($p.ExitCode)), см. bridge\logs\bridge.log" }
        Say "   bridge pid $($p.Id), log: bridge\logs\bridge.log" "   мост pid $($p.Id), журнал: bridge\logs\bridge.log"
    }

    Step 'Ready' 'Готово'
    Say 'In Witcher 3: load a save, press F4 for Steve mode (Minecraft hand, HUD and controls). Keep the game window focused.' 'В Ведьмаке: загрузи сейв, F4 — режим Стива (рука, HUD и управление Minecraft). Окно игры держи в фокусе.' 'Green'
    Say 'Stop: .\mcw.ps1 stop   (save and quit Witcher 3 from its menu)' 'Остановить: .\mcw.ps1 stop   (Ведьмака — сохраниться и выйти из меню)' 'Green'
}

function Invoke-Stop {
    $py = Find-Python
    Step 'Bridge' 'Мост'
    $b = Get-BridgeProcess
    if ($b) { Stop-Process -Id $b.ProcessId -Force; Say "   stopped pid $($b.ProcessId)" "   остановлен pid $($b.ProcessId)" } else { Say '   not running' '   не запущен' }
    Get-Process mcw_overlay -ErrorAction SilentlyContinue | Stop-Process -Force

    Step 'Minecraft client' 'Клиент Minecraft'
    & (Join-Path $Root 'mcclient\run_client.ps1') -Stop | ForEach-Object { Write-Host "   $_" -ForegroundColor DarkGray }

    Step 'Minecraft server' 'Сервер Minecraft'
    $s = Get-ServerProcess
    if (-not $s) { Say '   not running' '   не запущен' }
    else {
        $soft = $false
        if ($py) {
            Invoke-Quiet { & $py (Join-Path $Root 'mc\fake_bridge.py') stop } | Out-Null
            $soft = $s.WaitForExit(30000)
        }
        if ($soft) {
            Remove-Item -LiteralPath (Join-Path $Root 'mc\run\server.pid') -ErrorAction SilentlyContinue
            Say '   stopped, world saved' '   остановлен, мир сохранён'
        } else {
            & (Join-Path $Root 'mc\run_server.ps1') -Stop | Out-Null
            Say '   did not stop by itself in 30 s - killed (changes since the last autosave are lost)' '   сам за 30 с не остановился — убит (изменения с последнего автосохранения потеряны)' 'Yellow'
        }
    }

    $paths = if ($py) { Get-McwPaths $py } else { $null }
    if ($paths -and $paths.java) {
        $env:JAVA_HOME = $paths.java
        $null = & cmd /c "`"$(Join-Path $Root 'mcclient\gradlew.bat')`" --stop --quiet >nul 2>&1"
    }

    Step 'Witcher 3' 'Ведьмак 3'
    $w = Get-WitcherProcess
    if (-not $w) { Say '   not running' '   не запущен' }
    elseif ($CloseGame) { Stop-Process -Id $w.ProcessId -Force; Say '   closed' '   закрыт' }
    else { Say '   still running: save and quit from its menu (or .\mcw.ps1 stop -CloseGame)' '   ещё запущен: сохранись и выйди через меню (или .\mcw.ps1 stop -CloseGame)' }
}

function Invoke-Status {
    $rows = @(
        @((T 'Witcher 3' 'Ведьмак 3'), (Get-WitcherProcess)),
        @((T 'Minecraft server' 'Сервер Minecraft'), (Get-ServerProcess)),
        @((T 'Minecraft client' 'Клиент Minecraft'), (Get-ClientProcess)),
        @((T 'Bridge' 'Мост'), (Get-BridgeProcess)),
        @((T 'Overlay' 'Оверлей'), (Get-Process mcw_overlay -ErrorAction SilentlyContinue | Select-Object -First 1))
    )
    foreach ($r in $rows) {
        $p = $r[1]
        $id = if ($null -eq $p) { $null } elseif ($p.ProcessId) { $p.ProcessId } else { $p.Id }
        if ($id) { Write-Host ('{0,-20} {1}' -f $r[0], (T "running, pid $id" "работает, pid $id")) -ForegroundColor Green }
        else { Write-Host ('{0,-20} {1}' -f $r[0], (T 'not running' 'не запущен')) -ForegroundColor DarkGray }
    }
    $log = Join-Path $Root 'bridge\logs\bridge.log'
    if ((Get-BridgeProcess) -and (Test-Path -LiteralPath $log)) {
        $m = Get-Content -LiteralPath $log -Tail 200 -Encoding UTF8 | Where-Object { $_ -match 'bridge: метрики' } | Select-Object -Last 1
        if ($m) { Write-Host ''; Write-Host $m -ForegroundColor DarkGray }
    }
}

function Invoke-Uninstall {
    if ((Get-ServerProcess) -or (Get-ClientProcess) -or (Get-BridgeProcess)) { Invoke-Stop }
    $py = Find-Python
    $game = $env:MCW_W3_DIR
    if (-not $game -and $py) { $game = (Get-McwPaths $py).game }
    if (-not $game) { Fail 'Witcher 3 not found: set MCW_W3_DIR' 'Ведьмак не найден: задай MCW_W3_DIR' }
    Step 'Removing the mods from the game folder' 'Удаляю моды из папки игры'
    $mods = Join-Path $game 'mods'
    foreach ($n in 'modMCWBlocks', 'modMCWModels') {
        $d = Join-Path $mods $n
        if (Test-Path -LiteralPath $d) { Remove-Item -LiteralPath $d -Recurse -Force; Say "   removed $d" "   удалено $d" }
    }
    & (Join-Path $Root 'spike\uninstall_mod.ps1') -GamePath $game -Probe | ForEach-Object { Write-Host "   $_" }
    Set-LiteConfig $false
    if ($Purge) {
        foreach ($n in 'tex', 'redkit_work', 'redkit_models', 'logs', 'state.json') {
            $d = Join-Path $Data $n
            if (Test-Path -LiteralPath $d) { Remove-Item -LiteralPath $d -Recurse -Force; Say "   removed $d" "   удалено $d" }
        }
    } elseif (Test-Path -LiteralPath $StateFile) { Remove-Item -LiteralPath $StateFile }
    Say "Backups of your settings and saves stay in $Data\backups. The Minecraft world stays in mc\run\world." "Бэкапы настроек и сейвов остаются в $Data\backups. Мир Minecraft — в mc\run\world." 'Green'
}

# ---------------------------------------------------------------------------------------------- main
$oldOut = $null
try { $oldOut = [Console]::OutputEncoding; [Console]::OutputEncoding = New-Object Text.UTF8Encoding $false } catch {}
$env:PYTHONIOENCODING = 'utf-8'
try {
    switch ($Command) {
        'doctor' { if (-not (Invoke-Doctor)) { exit 1 } }
        'install' { Invoke-Install }
        'start' { Invoke-Start }
        'stop' { Invoke-Stop }
        'status' { Invoke-Status }
        'uninstall' { Invoke-Uninstall }
        'paths' { $py = Find-Python; if (-not $py) { Fail 'Python 3.11+ not found' 'Python 3.11+ не найден' }; Get-McwPaths $py | Format-List }
        default { Get-Help $PSCommandPath -Detailed | Out-String | Write-Host }
    }
} catch {
    Write-Host ''
    Write-Host ($_.Exception.Message) -ForegroundColor Red
    exit 1
} finally {
    if ($oldOut) { try { [Console]::OutputEncoding = $oldOut } catch {} }
}
