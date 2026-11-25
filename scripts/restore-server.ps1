<#
Восстановление HomeMusic из резервной копии в Google Drive (README, «Резервные копии»).

  pwsh -File .\scripts\restore-server.ps1                    # последняя копия
  pwsh -File .\scripts\restore-server.ps1 -Day 2026-11-20    # копия за этот день

Нужно: установленный HomeMusic (README, «Запуск с нуля») и .env, сохранённый со старого сервера, — в нём ключ, которым
зашифрованы пароли пользователей, и доступ к Google Drive. Работает и в Windows PowerShell 5.1.

Порядок: база данных запускается, Хаб останавливается → вход в Google (код вводится на google.com/device) → копия
базы заменяет текущую базу (спросит подтверждение) → загруженные вручную файлы и обложки возвращаются на место →
скачанная музыка ставится в очередь на скачивание заново → Хаб запускается. Пользователи входят заново, под
прежними логинами и паролями.
#>
param(
    [string]$Day = ''
)

$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Fail([string]$Text) {
    Write-Host $Text -ForegroundColor Red
    exit 1
}

if (-not (Test-Path (Join-Path $Root '.env'))) {
    Fail 'Нет файла .env. Положите в корень проекта .env, сохранённый со старого сервера.'
}
$envText = Get-Content (Join-Path $Root '.env') -Raw
if ($envText -notmatch '(?m)^\s*GOOGLE_DRIVE_CLIENT_ID\s*=\s*\S' -or $envText -notmatch '(?m)^\s*GOOGLE_DRIVE_CLIENT_SECRET\s*=\s*\S') {
    Fail 'В .env нет GOOGLE_DRIVE_CLIENT_ID и GOOGLE_DRIVE_CLIENT_SECRET — нужен .env со старого сервера.'
}
docker info *> $null
if ($LASTEXITCODE -ne 0) { Fail 'Docker не запущен.' }

Write-Host 'Собираю Хаб и запускаю базу данных…'
docker compose build hub
if ($LASTEXITCODE -ne 0) { Fail 'Хаб не собрался.' }
docker compose up -d --wait postgres navidrome
if ($LASTEXITCODE -ne 0) { Fail 'База данных не запустилась.' }
docker compose stop hub *> $null

$cmd = @('compose', 'run', '--rm', '--no-deps', 'hub', 'python', '-m', 'app.backup', 'restore')
if ($Day) { $cmd += @('--day', $Day) }
& docker @cmd
$code = $LASTEXITCODE

Write-Host 'Запускаю HomeMusic…'
docker compose up -d
if ($code -ne 0) { Fail 'Восстановление не завершилось — подробности выше. Его можно запустить ещё раз.' }
Write-Host 'Восстановлено. Пользователи входят заново; скачанная музыка докачивается в фоне («Загрузки»).' -ForegroundColor Green
