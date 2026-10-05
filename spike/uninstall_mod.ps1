<#
.SYNOPSIS
Удаляет modMCW (и modMCWProbe с -Probe) из <игра>\mods\.

.DESCRIPTION
Трогает только <игра>\mods\modMCW, <игра>\mods\modMCWProbe и пустую после этого <игра>\mods.
content0, bin и Documents не трогает. Про Documents\The Witcher 3\mods.settings только сообщает: если его
нет в manifest.txt последнего бэкапа (его создала игра), советует удалить, иначе — вернуть из бэкапа.
-ProbeOnly убирает одну пробу, когда она не скомпилировалась.
-WhatIf печатает план и ничего не меняет.

.EXAMPLE
.\uninstall_mod.ps1 -WhatIf
.\uninstall_mod.ps1 -Probe
.\uninstall_mod.ps1 -ProbeOnly
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$GamePath = '',      # пусто — MCW_W3_DIR или поиск w3paths.py (Steam, GOG)
    [switch]$Probe,
    [switch]$ProbeOnly,
    [string]$BackupRoot = (Join-Path $env:LOCALAPPDATA 'mcw\backups'),   # куда пишет backup_userdata.ps1
    [string]$UserDir = (Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'The Witcher 3')   # только читается
)
$ErrorActionPreference = 'Stop'
if (-not $GamePath) { $GamePath = $env:MCW_W3_DIR }
if (-not $GamePath) {
    $py = if ($env:MCW_PYTHON) { $env:MCW_PYTHON } else { 'python' }
    $GamePath = (& $py (Join-Path $PSScriptRoot 'w3paths.py') | Out-String | ConvertFrom-Json).game
}
if (-not $GamePath) { throw 'Witcher 3 не найден: задай -GamePath или MCW_W3_DIR' }

$exe = Join-Path $GamePath 'bin\x64_dx12\witcher3.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw "Не папка Witcher 3: нет $exe" }
$mods = Join-Path $GamePath 'mods'
$names = @()
if (-not $ProbeOnly) { $names += 'modMCW' }
if ($Probe -or $ProbeOnly) { $names += 'modMCWProbe' }

if (Get-Process -Name witcher3 -ErrorAction SilentlyContinue) {
    Write-Warning 'witcher3.exe запущен: удаление подействует после перезапуска игры'
}

foreach ($n in $names) {
    $dst = Join-Path $mods $n
    if ((Split-Path -Parent $dst) -ne $mods -or $n -notmatch '^modMCW(Probe)?$') { throw "отказ: $dst" }
    if (-not (Test-Path -LiteralPath $dst)) { Write-Output "нет: $dst"; continue }
    if ($PSCmdlet.ShouldProcess($dst, 'Удалить мод')) {
        Remove-Item -LiteralPath $dst -Recurse -Force
        Write-Output "удалено: $dst"
    }
}

$left = Join-Path $mods 'modMCWProbe'
if (-not ($Probe -or $ProbeOnly) -and (Test-Path -LiteralPath $left)) {
    Write-Warning "осталась проба $left (она компилируется сама по себе); убрать: .\uninstall_mod.ps1 -ProbeOnly"
}

if ((Test-Path -LiteralPath $mods) -and -not (Get-ChildItem -LiteralPath $mods -Force | Select-Object -First 1)) {
    if ($PSCmdlet.ShouldProcess($mods, 'Удалить пустую папку mods')) {
        Remove-Item -LiteralPath $mods -Force
        Write-Output "удалено (пустая): $mods"
    }
}

# mods.settings: файл не трогаем, только говорим, что с ним сделать. robocopy из бэкапа (launch.md, «Откат»)
# новые файлы не удаляет, поэтому смотрим, был ли mods.settings в manifest.txt последнего бэкапа.
$ms = Join-Path $UserDir 'mods.settings'
if (Test-Path -LiteralPath $ms) {
    $hasMcw = Select-String -LiteralPath $ms -Pattern 'modMCW' -SimpleMatch -Quiet
    $last = $null
    if (Test-Path -LiteralPath $BackupRoot) {
        $last = Get-ChildItem -LiteralPath $BackupRoot -Directory |
            Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'manifest.txt') } |
            Sort-Object Name | Select-Object -Last 1
    }
    if (-not $last) {
        Write-Output "есть $ms (modMCW: $hasMcw), бэкапа в $BackupRoot нет: был ли файл до мода, неизвестно, решай сам"
    } else {
        $manifest = Join-Path $last.FullName 'manifest.txt'
        $inBackup = Select-String -LiteralPath $manifest -Pattern '^mods\.settings\t' -Quiet
        if ($inBackup) {
            Write-Output ("{0} был до мода (modMCW сейчас: {1}). Вернуть из бэкапа (игра закрыта):" -f $ms, $hasMcw)
            Write-Output ("  Copy-Item -LiteralPath `"{0}`" -Destination `"{1}`"" -f (Join-Path $last.FullName 'mods.settings'), $ms)
        } else {
            Write-Output ("{0} появился после бэкапа {1} (его создала игра). Удалить (игра закрыта):" -f $ms, $last.Name)
            Write-Output "  Remove-Item -LiteralPath `"$ms`""
        }
    }
}
