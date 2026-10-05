<#
.SYNOPSIS
  Запуск и остановка headless-сервера MCW (Minecraft 1.21.11 + Fabric) в mc\run\.

.DESCRIPTION
  Сервер слушает только 127.0.0.1 (server.properties.mcw), мод MCW — мост на 127.0.0.1:25600.
  Перед запуском server.properties.mcw копируется в run\server.properties. Процесс скрытый, вывод — в
  run\logs\console.out.log / console.err.log (лог Minecraft — run\logs\latest.log), PID — в run\server.pid.
  По умолчанию скрипт ждёт строку "Done (...)!" и печатает JSON {"pid","wall_s","done_s"}.

  EULA Minecraft принимает пользователь сам: run\eula.txt с eula=true скрипт не создаёт.

.EXAMPLE
  .\run_server.ps1                  # запустить и дождаться Done
  .\run_server.ps1 -PauseWhenEmpty 60   # с ванильной паузой пустого сервера (проверка, что Геральт держит мир)
  .\run_server.ps1 -Stop            # погасить по PID (жёстко; мягко — команда stop через мост)
#>
param(
    [switch]$Stop,
    [switch]$NoWait,
    [int]$PauseWhenEmpty = -1,
    [int]$TimeoutSec = 240,
    [string]$Xmx = '2G'
)
$ErrorActionPreference = 'Stop'
$run = Join-Path $PSScriptRoot 'run'
$pidFile = Join-Path $run 'server.pid'

function Get-ServerProcess {
    if (-not (Test-Path $pidFile)) { return $null }
    $id = [int](Get-Content $pidFile -TotalCount 1)
    $p = Get-Process -Id $id -ErrorAction SilentlyContinue
    if ($null -ne $p -and $p.ProcessName -like 'java*') { return $p }
    return $null
}

if ($Stop) {
    $p = Get-ServerProcess
    if ($null -eq $p) { Write-Output 'server not running'; Remove-Item $pidFile -ErrorAction SilentlyContinue; return }
    Stop-Process -Id $p.Id -Force -Confirm:$false
    $p.WaitForExit(15000) | Out-Null
    Remove-Item $pidFile -ErrorAction SilentlyContinue
    Write-Output "stopped pid $($p.Id)"
    return
}

# JDK 21+: MCW_JAVA_HOME (его ставит ..\mcw.ps1), иначе поиск ..\spike\w3paths.py (JAVA_HOME, рантайм лаунчера
# Minecraft, Program Files); java в PATH бывает 1.8
$jdk = $env:MCW_JAVA_HOME
if (-not $jdk) {
    $py = if ($env:MCW_PYTHON) { $env:MCW_PYTHON } else { 'python' }
    $jdk = (& $py (Join-Path $PSScriptRoot '..\spike\w3paths.py') | Out-String | ConvertFrom-Json).java
}
if (-not $jdk) { throw 'нет JDK 21+ (задай MCW_JAVA_HOME; см. README, Requirements)' }
$env:JAVA_HOME = $jdk
$java = Join-Path $jdk 'bin\java.exe'
if (-not (Test-Path $java)) { throw "нет $java (задай MCW_JAVA_HOME = JDK 21)" }

$eula = Join-Path $run 'eula.txt'
if (-not (Test-Path $eula) -or -not (Select-String -Path $eula -Pattern '^eula=true' -Quiet)) {
    throw "run\eula.txt без eula=true: принять EULA Minecraft (https://aka.ms/MinecraftEULA) может только пользователь"
}
if (-not (Test-Path (Join-Path $run 'fabric-server-launch.jar'))) { throw 'нет run\fabric-server-launch.jar (см. README)' }
$mods = Get-ChildItem (Join-Path $run 'mods') -Filter *.jar -ErrorAction SilentlyContinue | ForEach-Object Name
if (-not ($mods -like 'mcw-*.jar') -or -not ($mods -like 'fabric-api-*.jar')) { throw "в run\mods нужны mcw-*.jar и fabric-api-*.jar, есть: $mods" }

$old = Get-ServerProcess
if ($null -ne $old) { throw "сервер уже запущен, pid $($old.Id) (.\run_server.ps1 -Stop)" }

$props = Get-Content (Join-Path $PSScriptRoot 'server.properties.mcw') -Encoding UTF8
if ($PauseWhenEmpty -ge 0) {
    $props = $props -replace '^pause-when-empty-seconds=.*$', "pause-when-empty-seconds=$PauseWhenEmpty"
}
[IO.File]::WriteAllLines((Join-Path $run 'server.properties'), $props, (New-Object Text.UTF8Encoding $false))

$logs = Join-Path $run 'logs'
New-Item -ItemType Directory -Force $logs | Out-Null
$out = Join-Path $logs 'console.out.log'
$err = Join-Path $logs 'console.err.log'
$t0 = Get-Date
$p = Start-Process -FilePath $java -ArgumentList "-Xmx$Xmx", '-jar', 'fabric-server-launch.jar', 'nogui' `
    -WorkingDirectory $run -RedirectStandardOutput $out -RedirectStandardError $err -WindowStyle Hidden -PassThru
Set-Content -Path $pidFile -Value $p.Id -Encoding ascii
if ($NoWait) { Write-Output ('{"pid":' + $p.Id + '}'); return }

$deadline = $t0.AddSeconds($TimeoutSec)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 250
    if ($p.HasExited) { throw "сервер вышел с кодом $($p.ExitCode), см. $out" }
    $m = Select-String -Path $out -Pattern 'Done \(([\d.,]+)s\)!' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -ne $m) {
        $wall = [math]::Round(((Get-Date) - $t0).TotalSeconds, 2)
        $done = $m.Matches[0].Groups[1].Value -replace ',', '.'
        Write-Output ('{"pid":' + $p.Id + ',"wall_s":' + ($wall.ToString([Globalization.CultureInfo]::InvariantCulture)) + ',"done_s":' + $done + '}')
        return
    }
}
throw "нет Done за $TimeoutSec с, см. $out (pid $($p.Id) не гашу — .\run_server.ps1 -Stop)"
