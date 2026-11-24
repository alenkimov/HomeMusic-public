<#
Обновление HomeMusic на сервере (Windows, Docker Desktop): стягивает новые коммиты master и пересобирает то,
что они затронули. Ничего нового — ничего не делает, так что его можно запускать по расписанию.

  pwsh -File .\scripts\update-server.ps1              # обновить сейчас (если есть что)
  pwsh -File .\scripts\update-server.ps1 -Force       # пересобрать Хаб и перезапустить, даже если коммитов нет
  pwsh -File .\scripts\update-server.ps1 -Install     # проверять каждые 15 минут (задача Планировщика Windows)
  pwsh -File .\scripts\update-server.ps1 -Install -Minutes 30
  pwsh -File .\scripts\update-server.ps1 -Uninstall   # убрать задачу

Работает и в Windows PowerShell 5.1 (powershell.exe вместо pwsh).

Порядок: Docker жив → git fetch → в рабочей папке нет изменённых отслеживаемых файлов → master догоняет
origin/master без слияния → идут загрузки? тогда обновление откладывается (до часа, потом всё равно) →
git merge --ff-only → docker compose build того, что изменилось с последнего удачного обновления (hub — при
правках в hub/ и web/, embedder — в embedder/, tunnel — в gateway/tunnel/; -Force — ещё и hub) →
docker compose up -d → ждёт /healthz Хаба → запоминает развёрнутый коммит → docker image prune -f.
Журнал — update.log в корне проекта (в git не попадает). Код выхода 0 — обновлено или нечего обновлять,
1 — ошибка (подробности в журнале).
#>
param(
    [switch]$Force,
    [switch]$Install,
    [switch]$Uninstall,
    [int]$Minutes = 15
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Log = Join-Path $Root 'update.log'
$TaskName = 'HomeMusic update'
$Branch = 'master'
$HubUrl = 'http://localhost:8080/healthz'
# сколько обновление ждёт конца загрузок: потом обновляется всё равно (прерванные загрузки Хаб продолжает сам)
$MaxWaitForDownloads = [TimeSpan]::FromHours(1)

function Write-Log([string]$Text) {
    $line = '{0:yyyy-MM-dd HH:mm:ss}  {1}' -f (Get-Date), $Text
    Write-Host $line
    # журнал не растёт бесконечно: больше 1 МБ — начинается заново, прежний остаётся как update.log.1
    if ((Test-Path $Log) -and (Get-Item $Log).Length -gt 1MB) { Move-Item $Log "$Log.1" -Force }
    Add-Content -Path $Log -Value $line -Encoding UTF8
}

function Invoke-Native([string]$What, [scriptblock]$Command) {
    # внешняя команда: её вывод — в журнал, ненулевой код — ошибка
    $out = & $Command 2>&1 | ForEach-Object { "$_" }
    foreach ($l in $out) { if ($l.Trim()) { Write-Log "    $l" } }
    if ($LASTEXITCODE -ne 0) { throw "$What завершилось с кодом $LASTEXITCODE" }
    return $out
}

if ($Install) {
    $shell = if (Get-Command pwsh -ErrorAction SilentlyContinue) { (Get-Command pwsh).Source } else { (Get-Command powershell).Source }
    $action = New-ScheduledTaskAction -Execute $shell -WorkingDirectory $Root `
        -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    $every = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
        -RepetitionInterval (New-TimeSpan -Minutes $Minutes) -RepetitionDuration (New-TimeSpan -Days 3650)
    $logon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    # Docker Desktop живёт, пока пользователь вошёл в Windows: задача — от него же, только пока он вошёл
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Hours 2) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($every, $logon) -Settings $settings `
        -Description "git pull + docker compose для HomeMusic ($Root)" -Force | Out-Null
    Write-Host "Задача «$TaskName» установлена: каждые $Minutes мин и при входе в Windows. Журнал: $Log"
    exit 0
}
if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Задача «$TaskName» убрана."
    exit 0
}

Set-Location $Root
$lockPath = Join-Path $Root '.git\homemusic-update.lock'
$pendingPath = Join-Path $Root '.git\homemusic-update-pending'
# последний коммит, с которым сервер поднялся и ответил: что пересобирать, считается от него
$deployedPath = Join-Path $Root '.git\homemusic-deployed'
$lock = $null
try {
    # второй запуск, пока идёт первый (сборка долгая), сразу выходит
    try { $lock = [IO.File]::Open($lockPath, 'OpenOrCreate', 'ReadWrite', 'None') }
    catch { Write-Host 'Обновление уже идёт.'; exit 0 }

    & docker info *> $null
    if ($LASTEXITCODE -ne 0) { Write-Log 'Docker не отвечает (Docker Desktop не запущен?) — пропуск.'; exit 1 }

    Invoke-Native 'git fetch' { git fetch --quiet origin $Branch } | Out-Null
    $here = (git rev-parse --abbrev-ref HEAD).Trim()
    if ($here -ne $Branch) { throw "Сейчас ветка $here, а обновляется ${Branch}: git checkout $Branch" }

    $dirty = @(git status --porcelain --untracked-files=no)
    if ($dirty.Count) {
        throw ("В рабочей папке изменены отслеживаемые файлы — закоммитьте их или уберите (git checkout -- <файл>):`n" + ($dirty -join "`n"))
    }

    $head = (git rev-parse HEAD).Trim()
    $remote = (git rev-parse "origin/$Branch").Trim()
    # от последнего удачного обновления считается, что собирать: запуск, упавший после pull, не теряет этого.
    # Отметки ещё нет (первый запуск) — развёрнутым считается то, что сейчас в папке
    $deployed = if (Test-Path $deployedPath) { (Get-Content $deployedPath -Raw).Trim() } else { $head }
    & git cat-file -e "$deployed^{commit}" 2>$null
    if ($LASTEXITCODE -ne 0) { $deployed = $head }
    if ($head -eq $remote -and $deployed -eq $head -and -not $Force) {
        if (Test-Path $pendingPath) { Remove-Item $pendingPath }
        exit 0  # нечего обновлять: в журнал не пишется, чтобы он не рос от запусков по расписанию
    }
    if ($head -ne $remote) {
        & git merge-base --is-ancestor $head $remote
        if ($LASTEXITCODE -ne 0) { throw "Локальный $Branch разошёлся с origin/$Branch (есть коммиты только здесь): разберитесь вручную." }
    }

    # загрузки идут: Хаб перезапустится посреди них — подождать, но не дольше часа
    $active = 0
    $count = & docker exec homemusic_hub python -c "from app.db import get_database_url;from sqlalchemy import create_engine;c=create_engine(get_database_url()).connect();print(c.exec_driver_sql('SELECT count(*) FROM download_items WHERE status IN '+repr(('queued','searching','downloading','tagging','importing'))).scalar_one())" 2>$null
    if ($LASTEXITCODE -eq 0) { [int]::TryParse("$count".Trim(), [ref]$active) | Out-Null }
    if ($active -gt 0 -and -not $Force) {
        if (-not (Test-Path $pendingPath)) { Set-Content $pendingPath (Get-Date).ToString('o') }
        $since = [DateTime]::Parse((Get-Content $pendingPath -Raw).Trim())
        if ((Get-Date) - $since -lt $MaxWaitForDownloads) {
            Write-Log "Есть обновление, но идут загрузки ($active) — отложено."
            exit 0
        }
        Write-Log "Загрузки идут дольше часа ($active) — обновляю всё равно, Хаб продолжит их после перезапуска."
    }

    $changed = @(git diff --name-only $deployed $remote)
    if ($head -ne $remote) {
        Write-Log ("Обновление {0} → {1}:" -f $head.Substring(0, 7), $remote.Substring(0, 7))
        foreach ($c in @(git log --oneline "$head..$remote")) { Write-Log "    $c" }
        Invoke-Native 'git merge' { git merge --ff-only --quiet "origin/$Branch" } | Out-Null
    } elseif ($deployed -ne $head) {
        Write-Log ("Прошлое обновление до {0} не закончилось — довожу." -f $head.Substring(0, 7))
    } else {
        Write-Log 'Пересборка без новых коммитов (-Force).'
    }

    # что пересобрать: по путям изменённых файлов; -Force — ещё и Хаб (embedder долгий — только когда он менялся)
    $build = @()
    if ($Force -or ($changed | Where-Object { $_ -match '^(hub|web)/' })) { $build += 'hub' }
    if ($changed | Where-Object { $_ -match '^embedder/' }) { $build += 'embedder' }
    if ($changed | Where-Object { $_ -match '^gateway/tunnel/' }) { $build += 'tunnel' }
    if ($build.Count) {
        Write-Log ('Сборка: ' + ($build -join ', '))
        Invoke-Native 'docker compose build' { docker compose build @build } | Out-Null
    }
    # up -d применяет и новые образы, и правки docker-compose.yml/.env; чужие контейнеры не трогает
    Invoke-Native 'docker compose up' { docker compose up -d } | Out-Null

    $ok = $false
    for ($i = 0; $i -lt 36; $i++) {
        Start-Sleep -Seconds 5
        try { if ((Invoke-WebRequest -Uri $HubUrl -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200) { $ok = $true; break } } catch {}
    }
    if (-not $ok) {
        throw "Хаб не ответил за 3 минуты: docker logs --tail 100 homemusic_hub. Вернуть прежнюю версию: git reset --hard $head; docker compose up -d --build"
    }
    Set-Content $deployedPath (git rev-parse HEAD).Trim()
    & docker image prune -f *> $null  # прежние образы, оставшиеся без имени после сборки
    if (Test-Path $pendingPath) { Remove-Item $pendingPath }
    Write-Log ('Готово: работает {0}.' -f (git rev-parse --short HEAD).Trim())
    exit 0
}
catch {
    Write-Log "ОШИБКА: $($_.Exception.Message)"
    exit 1
}
finally {
    if ($lock) { $lock.Close() }
}
