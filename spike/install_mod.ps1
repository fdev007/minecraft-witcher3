<#
.SYNOPSIS
Копирует modMCW (и modMCWProbe с -Probe) из spike\mod в <игра>\mods\.

.DESCRIPTION
Трогает только <игра>\mods\modMCW и <игра>\mods\modMCWProbe (и создаёт <игра>\mods, если её нет).
content0, bin и Documents не трогает. Старую копию мода сначала удаляет целиком, чтобы в игре не осталось
лишних .ws. Перед копированием проверяет, что все .ws в UTF-8 без BOM (так требует 5.0).
-WhatIf печатает план и ничего не меняет.

.EXAMPLE
.\install_mod.ps1 -WhatIf
.\install_mod.ps1
.\install_mod.ps1 -Probe
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$GamePath = '',      # пусто — MCW_W3_DIR или поиск w3paths.py (Steam, GOG)
    [switch]$Probe,
    [string]$Source = ''         # папка с modMCW и modMCWProbe; пусто — mod рядом со скриптом
)
$ErrorActionPreference = 'Stop'
if (-not $GamePath) { $GamePath = $env:MCW_W3_DIR }
if (-not $GamePath) {
    $py = if ($env:MCW_PYTHON) { $env:MCW_PYTHON } else { 'python' }
    $GamePath = (& $py (Join-Path $PSScriptRoot 'w3paths.py') | Out-String | ConvertFrom-Json).game
}
if (-not $GamePath) { throw 'Witcher 3 не найден: задай -GamePath или MCW_W3_DIR' }
if (-not $Source) { $Source = Join-Path $PSScriptRoot 'mod' }   # не в param: там $PSScriptRoot при -File пуст (5.1)

$exe = Join-Path $GamePath 'bin\x64_dx12\witcher3.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw "Не папка Witcher 3: нет $exe" }
# Абсолютный путь: DirectoryName у файлов абсолютный, относительный -Source (.\mod) иначе не сравнить
$srcRoot = (Resolve-Path -LiteralPath $Source).Path
$mods = Join-Path $GamePath 'mods'
$names = @('modMCW')
if ($Probe) { $names += 'modMCWProbe' }

# Проверка исходников: папка есть, .ws лежат в content\scripts\local, кодировка UTF-8 без BOM
$utf8 = New-Object System.Text.UTF8Encoding($false, $true)   # строгий декодер: бросает на битых байтах
foreach ($n in $names) {
    $src = Join-Path $srcRoot $n
    # С разделителем на конце: иначе папка local2 сошла бы за local
    $local = (Join-Path $src 'content\scripts\local').TrimEnd('\') + '\'
    $ws = @(Get-ChildItem -LiteralPath $src -Recurse -Filter '*.ws' -File -ErrorAction SilentlyContinue)
    if ($ws.Count -eq 0) { throw "Нет .ws в $src (мод ещё не собран?)" }
    foreach ($f in $ws) {
        if (-not ($f.DirectoryName + '\').StartsWith($local, [StringComparison]::OrdinalIgnoreCase)) {
            throw "$($f.FullName): .ws вне content\scripts\local"
        }
        $b = [IO.File]::ReadAllBytes($f.FullName)
        if ($b.Length -ge 3 -and $b[0] -eq 0xEF -and $b[1] -eq 0xBB -and $b[2] -eq 0xBF) { throw "$($f.FullName): UTF-8 с BOM" }
        if ($b.Length -ge 2 -and (($b[0] -eq 0xFF -and $b[1] -eq 0xFE) -or ($b[0] -eq 0xFE -and $b[1] -eq 0xFF))) {
            throw "$($f.FullName): UTF-16"
        }
        try { [void]$utf8.GetString($b) } catch { throw "$($f.FullName): не UTF-8" }
    }
    Write-Output ("источник {0}: {1} .ws, проверено" -f $n, $ws.Count)
}

if (Get-Process -Name witcher3 -ErrorAction SilentlyContinue) {
    Write-Warning 'witcher3.exe запущен: скрипты компилируются при старте, изменения подхватятся после перезапуска'
}

if (-not (Test-Path -LiteralPath $mods)) {
    if ($PSCmdlet.ShouldProcess($mods, 'Создать папку mods')) {
        New-Item -ItemType Directory -Path $mods | Out-Null
        Write-Output "создано: $mods"
    }
}

foreach ($n in $names) {
    $src = Join-Path $srcRoot $n
    $dst = Join-Path $mods $n
    # Удалять можно только <игра>\mods\modMCW*
    if ((Split-Path -Parent $dst) -ne $mods -or $n -notmatch '^modMCW(Probe)?$') { throw "отказ: $dst" }
    if (Test-Path -LiteralPath $dst) {
        if ($PSCmdlet.ShouldProcess($dst, 'Удалить старую копию мода')) {
            Remove-Item -LiteralPath $dst -Recurse -Force
            Write-Output "удалено: $dst"
        }
    }
    if ($PSCmdlet.ShouldProcess($dst, "Скопировать из $src")) {
        Copy-Item -LiteralPath $src -Destination $dst -Recurse
        Write-Output "скопировано: $src -> $dst"
        Get-ChildItem -LiteralPath $dst -Recurse -File | ForEach-Object {
            Write-Output ("  {0} ({1} Б)" -f $_.FullName.Substring($dst.Length + 1), $_.Length)
        }
    }
}
Write-Output 'Дальше: первый запуск с модом с флагом -forcescriptcompilation (см. launch.md), потом python w3_preflight.py'
