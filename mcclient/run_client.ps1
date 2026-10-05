<#
.SYNOPSIS
  Запуск и остановка скрытого клиента Minecraft 1.21.11 (мод mcwc) для мэшапа с Ведьмаком.

.DESCRIPTION
  Клиент запускается из dev-окружения Loom (gradlew runClient): свой mcclient\run\, ник Geralt, сразу заходит на
  127.0.0.1:25565 (сначала mc\run_server.ps1). Окно появляется без фокуса под всеми окнами — Ведьмак не теряет фокус;
  место и размер окну задаёт оверлей, который запускает мост. Вывод Gradle — run\logs\gradle.out.log, лог Minecraft —
  run\logs\latest.log. Скрипт ждёт строку мода «жду мост» и печатает JSON {"pid","wall_s"} (pid — JVM игры).

  Первый запуск Loom докачивает клиент и ассеты (сотни МБ в %USERPROFILE%\.gradle).

.EXAMPLE
  .\run_client.ps1                  # запустить и дождаться мода
  .\run_client.ps1 -Sound           # со звуком (по умолчанию звук клиента выключен, окна ведь не видно)
  .\run_client.ps1 -Stop            # погасить JVM игры (Gradle выйдет сам)
#>
param(
    [switch]$Stop,
    [switch]$Sound,
    [switch]$Decorated,
    [int]$TimeoutSec = 300
)
$ErrorActionPreference = 'Stop'
$run = Join-Path $PSScriptRoot 'run'

function Get-ClientProcess {
    # JVM игры — дочерний процесс демона Gradle, узнаём её по конфигу dev-запуска Loom этого проекта
    $cfg = [regex]::Escape((Join-Path $PSScriptRoot '.gradle\loom-cache\launch.cfg'))
    Get-CimInstance Win32_Process -Filter "Name='java.exe'" |
        Where-Object { $_.CommandLine -match "fabric\.dli\.config=$cfg" -and $_.CommandLine -match 'fabric\.dli\.env=client' }
}

if ($Stop) {
    $p = @(Get-ClientProcess)
    if ($p.Count -eq 0) { Write-Output 'client not running'; return }
    foreach ($x in $p) {
        Stop-Process -Id $x.ProcessId -Force -Confirm:$false
        Write-Output "stopped pid $($x.ProcessId)"
    }
    return
}

$old = @(Get-ClientProcess)
if ($old.Count -gt 0) { throw "клиент уже запущен, pid $($old[0].ProcessId) (.\run_client.ps1 -Stop)" }

# JDK 21+ — как в mc\run_server.ps1: MCW_JAVA_HOME (его ставит ..\mcw.ps1), иначе поиск ..\spike\w3paths.py
$jdk = $env:MCW_JAVA_HOME
if (-not $jdk) {
    $py = if ($env:MCW_PYTHON) { $env:MCW_PYTHON } else { 'python' }
    $jdk = (& $py (Join-Path $PSScriptRoot '..\spike\w3paths.py') | Out-String | ConvertFrom-Json).java
}
if (-not $jdk -or -not (Test-Path (Join-Path $jdk 'bin\java.exe'))) { throw "нет JDK 21+: $jdk (задай MCW_JAVA_HOME; см. README, Requirements)" }
$env:JAVA_HOME = $jdk

$logs = Join-Path $run 'logs'
New-Item -ItemType Directory -Force $logs | Out-Null
$out = Join-Path $logs 'gradle.out.log'
$err = Join-Path $logs 'gradle.err.log'
$latest = Join-Path $logs 'latest.log'
$gradleArgs = @('runClient', '--console=plain')
$vm = @()
if ($Sound) { $vm += '-Dmcwc.sound=true' }
if ($Decorated) { $vm += '-Dmcwc.decorated=true' }
if ($vm.Count -gt 0) { $gradleArgs += "-PmcwcVm=$($vm -join ',')" }   # build.gradle: runs.client.vmArgs
$t0 = Get-Date
$p = Start-Process -FilePath (Join-Path $PSScriptRoot 'gradlew.bat') -ArgumentList $gradleArgs -WorkingDirectory $PSScriptRoot `
    -RedirectStandardOutput $out -RedirectStandardError $err -WindowStyle Hidden -PassThru

$deadline = $t0.AddSeconds($TimeoutSec)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 500
    if ($p.HasExited) { throw "Gradle вышел с кодом $($p.ExitCode), см. $out" }
    if ((Test-Path $latest) -and (Get-Item $latest).LastWriteTime -ge $t0 -and
        (Select-String -Path $latest -Pattern 'mcwc: жду мост' -Quiet -Encoding UTF8)) {
        $c = @(Get-ClientProcess)
        $wall = [math]::Round(((Get-Date) - $t0).TotalSeconds, 2)
        $id = if ($c.Count -gt 0) { $c[0].ProcessId } else { 0 }
        Write-Output ('{"pid":' + $id + ',"wall_s":' + ($wall.ToString([Globalization.CultureInfo]::InvariantCulture)) + '}')
        return
    }
}
throw "мод клиента не ответил за $TimeoutSec с, см. $out и $latest"
