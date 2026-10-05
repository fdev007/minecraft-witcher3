<#
.SYNOPSIS
Бэкап Documents\The Witcher 3 (*.settings, сейвы) в %LOCALAPPDATA%\mcw\backups\<время> перед запуском с модом.

.DESCRIPTION
Исходники только читает. Копирует файлы *.settings и *.settings.bak из корня Documents\The Witcher 3
и папку gamesaves целиком, рядом пишет manifest.txt (путь, размер, SHA256). scriptslog.txt и прочие логи
не копирует. В конце печатает команду восстановления. -WhatIf печатает план и ничего не пишет.

.EXAMPLE
.\backup_userdata.ps1 -WhatIf
.\backup_userdata.ps1
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$Source = (Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'The Witcher 3'),
    [string]$Destination = (Join-Path $env:LOCALAPPDATA 'mcw\backups')
)
$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $Source)) { throw "Нет $Source" }
$src = (Resolve-Path -LiteralPath $Source).Path.TrimEnd('\')
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$dst = Join-Path $Destination $stamp

$files = @(Get-ChildItem -LiteralPath $src -File | Where-Object { $_.Name -like '*.settings' -or $_.Name -like '*.settings.bak' })
$saves = Join-Path $src 'gamesaves'
if (Test-Path -LiteralPath $saves) { $files += @(Get-ChildItem -LiteralPath $saves -Recurse -File) }
if ($files.Count -eq 0) { throw "В $src нечего копировать" }
$total = ($files | Measure-Object -Property Length -Sum).Sum
Write-Output ("источник: {0}; файлов {1}, {2:N0} Б" -f $src, $files.Count, $total)

if (Get-Process -Name witcher3 -ErrorAction SilentlyContinue) {
    Write-Warning 'witcher3.exe запущен: игра может писать в settings и сейвы прямо сейчас'
}

if ($PSCmdlet.ShouldProcess($dst, "Скопировать $($files.Count) файлов из $src")) {
    New-Item -ItemType Directory -Path $dst | Out-Null
    $manifest = New-Object System.Collections.Generic.List[string]
    $manifest.Add("source`t$src")
    foreach ($f in $files) {
        $rel = $f.FullName.Substring($src.Length + 1)
        $to = Join-Path $dst $rel
        $dir = Split-Path -Parent $to
        if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
        Copy-Item -LiteralPath $f.FullName -Destination $to
        $hash = (Get-FileHash -LiteralPath $to -Algorithm SHA256).Hash
        $manifest.Add("$rel`t$($f.Length)`t$hash")
    }
    [IO.File]::WriteAllLines((Join-Path $dst 'manifest.txt'), $manifest)
    Write-Output "скопировано в: $dst (manifest.txt рядом)"
    # robocopy сливает папки надёжнее Copy-Item; сейвы, сделанные после бэкапа, не удаляет
    Write-Output 'восстановить (игра закрыта):'
    Write-Output "  robocopy `"$dst`" `"$src`" /E /XF manifest.txt"
}
