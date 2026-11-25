# HomeMusic

Личный музыкальный сервис на домашнем ПК: библиотека и стриминг — **Navidrome**, поиск, скачивание и загрузка своих
файлов — свой сервис **Хаб** (FastAPI + PWA). API Хаба — [hub/API.md](hub/API.md),
устройство — [ARCHITECTURE.md](ARCHITECTURE.md).

| Сервис (`docker-compose.yml`) | Что делает |
|---|---|
| `navidrome` | сканирует `MUSIC_ROOT/library`, стримит, отдаёт Subsonic API сторонним плеерам (Tempus и др.) |
| `hub` | Хаб: API и PWA на `:8080`; при старте сам применяет миграции своей базы |
| `postgres` | база Хаба |
| `youtube-pot` | PO-токены для yt-dlp (только внутри сети Compose) |
| `embedder` | звуковые векторы для миксов и «Моей волны» (GPU, по желанию — см. ниже) |
| `tunnel` / `caddy` | доступ снаружи: профиль `tunnel` (через VPS) или `direct` (публичный IP), см. «Доступ снаружи» |

В браузере Хаб — весь сервис: Главная, библиотека Navidrome («Понравившиеся», плейлисты, исполнители, альбомы)
и плеер с очередью. Логин и пароль везде одни — учётка Navidrome.

## Запуск с нуля

1. Docker (на Windows — Docker Desktop с WSL2, автозапуск при входе; сон ПК отключить) и Git.
2. Папка музыки, например `B:\Music`, с подпапками `library`, `staging`, `.trash`, `.backups`.
3. Настройки:
   ```
   copy .env.example .env          # заполнить (см. комментарии в файле)
   copy config.example.yaml config.yaml
   ```
   Обязательны `MUSIC_ROOT`, `ND_PASSWORDENCRYPTIONKEY` (случайная строка, потом не менять), `NAVIDROME_ADMIN_PASSWORD`,
   `HUB_DB_PASSWORD` (случайный) и `HUB_SECRET_KEY` (ключ Fernet, команда генерации — в `.env.example`).
   `LLM_API_KEY` — ключ OpenRouter для ИИ: перевод текстов песен, подбор миксов и «Наведение порядка» (без него они
   работают проще; «Наведение порядка» тратит не больше `TIDY_AI_USD_MONTH` в месяц, по умолчанию $2).
   Ключ Last.fm администратор добавляет в Хабе: «Меню → Администрирование → Сторонние сервисы».
4. Доступ снаружи — один из вариантов ниже (или никакого, если сервис нужен только в домашней сети).
5. Запуск:
   ```
   docker compose up -d --build
   ```
6. Первый запуск Navidrome: создать админа на `http://localhost:4533` с логином и паролем из `.env`
   (`NAVIDROME_ADMIN_USER` / `NAVIDROME_ADMIN_PASSWORD`): через него Хаб запускает сканирование и заводит пользователей.
7. Телефон: открыть адрес Хаба в Chrome → меню → «Установить приложение». После этого Хаб есть в меню «Поделиться»
   Android: аудиофайл → загрузка в библиотеку, ссылка или название трека → поиск. Сторонний Subsonic-плеер (например,
   [Tempus](https://github.com/eddyizm/tempus/releases)) подключается к адресу Navidrome.

### Доступ снаружи

**Публичный статический IP (`direct`).** В `.env`: `COMPOSE_PROFILES=direct`, `PUBLIC_DOMAIN=<домен>`,
`PUBLIC_HUB_URL=https://<домен>`, `PUBLIC_MUSIC_URL=` пустой. A-запись домена указывает на домашний IP, роутер
пробрасывает 80 и 443 на этот ПК. Caddy (`gateway/direct/Caddyfile`) сам получает сертификат Let's Encrypt и отдаёт
только Хаб; сторонние плееры ходят через Хаб (`/rest`).

**Через VPS (`tunnel`).** Обратный SSH-туннель с ПК на VPS, на VPS Caddy с сертификатом на IP:

```
Телефон ──HTTPS──> VPS 203.0.113.10 (Caddy)
                     :8444 → 127.0.0.1:4533 ─┐
                     :8445 → 127.0.0.1:8080 ─┤ обратный SSH-туннель (пользователь homemusic-tunnel)
                                             ▼
             ПК: homemusic_tunnel → navidrome:4533, hub:8080
```

1. В `.env`: `COMPOSE_PROFILES=tunnel`, `VPS_HOST=203.0.113.10`, `PUBLIC_MUSIC_URL=https://203.0.113.10:8444`,
   `PUBLIC_HUB_URL=https://203.0.113.10:8445`.
2. Ключ туннеля (один раз):
   ```
   ssh-keygen -t ed25519 -N "" -C homemusic-tunnel -f gateway\tunnel\tunnel_key
   ssh-keyscan -t ed25519 203.0.113.10 > gateway\tunnel\known_hosts
   ```
3. VPS (один раз, идемпотентно; другие сервисы на VPS не трогает, изменённые файлы бэкапит в `/root/homemusic-gateway-backup/`):
   ```
   scp gateway\vps\setup-vps.sh myvps:/root/homemusic-setup-vps.sh
   ssh myvps "bash /root/homemusic-setup-vps.sh 203.0.113.10 '<содержимое gateway\tunnel\tunnel_key.pub>'"
   ```
   Откат — в конце README.

## Отправка треков в Telegram

Администратор создаёт бота через [@BotFather](https://t.me/BotFather) и указывает его токен в `.env` как
`TELEGRAM_BOT_TOKEN`. Применить настройку можно командой `docker compose up -d hub`. Хаб сам получает обновления
этого бота через Bot API.

Пользователь один раз открывает «Меню» → «Telegram» → «Открыть бота» и нажимает Start. После этого в «Поделиться»
появляется личный чат «Мне». При переходе с личных ботов каждый пользователь подключается к общему боту заново.

Чтобы отправлять в группу, сделайте бота её администратором. Группу увидят пользователи Хаба с подтверждённым
членством. Если бот уже был администратором до подключения к Хабу, отправьте в группе `/homemusic@имя_бота`
с именем своего бота. В каналах право публикации требуется и боту, и пользователю. Хаб проверяет права перед каждой
отправкой через [getChatMember](https://core.telegram.org/bots/api#getchatmember).

## Обновление

**Автоматически** — `scripts/update-server.ps1` (PowerShell на сервере, из корня проекта):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\update-server.ps1 -Install   # проверять каждые 15 минут
powershell -ExecutionPolicy Bypass -File .\scripts\update-server.ps1           # обновить сейчас
```

`-Install` ставит задачу «HomeMusic update» в Планировщик Windows (каждые 15 минут и при входе в Windows;
`-Minutes 30` — реже, `-Uninstall` — убрать). Нет новых коммитов — скрипт ничего не делает. Есть — `git pull`
(только fast-forward), пересборка того, что затронуто (`hub` при правках в `hub/` и `web/`, `embedder` — в
`embedder/`), `docker compose up -d`, ожидание `/healthz` Хаба и `docker image prune -f`. Пока идут загрузки,
обновление откладывается, но не дольше часа. Изменённые на сервере отслеживаемые файлы останавливают его: их
нужно закоммитить или убрать (`git checkout -- <файл>`). Журнал — `update.log` в корне проекта.

**Вручную:**

```
git pull
docker compose up -d --build          # пересобрать Хаб (yt-dlp внутри образа обновляется пересборкой)
docker compose pull navidrome         # после смены версии в docker-compose.yml
docker image prune -f                 # убрать прежние версии образов, оставшиеся без имени (<none>) после пересборки
```

Пересборка под тем же тегом не удаляет прежний образ: он остаётся без имени и занимает место в диске Docker
(образ embedder — около 7 ГБ). `prune -f` (без `-a`) удаляет только такие образы, у которых нет контейнера;
образы с тегом, контейнеры и тома не трогает. Запускайте после каждого `docker compose build` / `up -d --build`.

yt-dlp ломается после изменений YouTube — пересобирайте образ Хаба примерно раз в неделю
(версия yt-dlp закреплена в `hub/pyproject.toml`: поднять её и выполнить `uv lock` в `hub/`).
При обновлении `bgutil-ytdlp-pot-provider` одновременно меняйте тег сервиса `youtube-pot` в
`docker-compose.yml`: плагин и сервер должны иметь одинаковую версию.

### YouTube просит проверку «не робот»

`Sign in to confirm you’re not a bot` означает, что YouTube отказал в выдаче файла: песня может находиться
поиском, но скачивание и прослушивание недоступны. Можно повторить
позже или выбрать файл из другого источника; повторный поиск того же ролика сам по себе проверку не снимает.

Каким клиентом спрашивать YouTube, выбирает сам yt-dlp (закрепить клиент можно в `ytdlp.player_clients`
конфига — пусто по умолчанию: закреплённый клиент устаревает, в ноябре 2026 `mweb` отдавал ссылки с HTTP 403).
PO-токены Хаб автоматически получает через
[bgutil-ytdlp-pot-provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider), как рекомендует
[руководство yt-dlp](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide). Сервис `youtube-pot` запускается
в Compose вместе с Хабом, доступен только внутри Docker и не получает файл cookies. Для диагностики доступности:
`docker compose exec hub python -c "import urllib.request; print(urllib.request.urlopen('http://youtube-pot:4416/ping', timeout=5).status)"`.
PO-токены не гарантируют обход проверки входа, HTTP 403 и ограничений аккаунта/IP.

Обычный YouTube и поиск перезаливов делят два слота; yt-dlp делает паузу 1 с между запросами и 5 с перед
скачиванием, повторяет сетевые ошибки один раз. Успешные метаданные ролика переиспользуются 10 минут,
одновременные запросы одного ролика объединяются; существующий кэш прослушивания сохраняется. После проверки
«не робот», недействительной сессии или HTTP 429 новые обращения YouTube прекращаются на 15 минут;
загрузки могут перейти к резервному SoundCloud. Параметры — `ytdlp` в `config.example.yaml`.

Если ошибка сохраняется, владелец сервера может подключить cookies YouTube:

1. Экспортировать cookies **только `youtube.com`** в формате Netscape по
   [инструкции yt-dlp](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies):
   войти в YouTube в отдельном приватном окне, в той же вкладке открыть `https://www.youtube.com/robots.txt`,
   экспортировать cookies и закрыть приватное окно. yt-dlp предупреждает о риске блокировки аккаунта;
   используйте отдельный аккаунт и подключайте его только при необходимости.
2. Сохранить файл на серверном ПК как `B:\Music\.youtube-cookies.txt` (при другом `MUSIC_ROOT` — в его корне).
   Cookies дают доступ к сессии: не отправлять их в чат и не добавлять в git.
3. В `.env` добавить `YTDLP_COOKIES_FILE=/music/.youtube-cookies.txt` — это путь внутри контейнера.
   Применить окружение командой `docker compose up -d hub`, дождавшись завершения активных загрузок.
4. Если yt-dlp сообщает `cookies are no longer valid` / `rotated`, заново выполнить шаг 1; продление дат в
   старом файле сессию не восстанавливает. Сначала полностью сохранить новый экспорт в отдельный локальный
   файл вне репозитория и закрыть приватное окно, затем заменить `B:\Music\.youtube-cookies.txt` готовым файлом
   (не редактировать его по строкам во время работы загрузчика). Ручной вход и CAPTCHA выполняет владелец;
   содержимое экспорта не отправлять агенту и не печатать в логах.
5. Повторить загрузку. Хаб замечает замену файла, сбрасывает паузу после отказа и кэш метаданных; перезапуск
   не нужен. Он читает cookies в приватную копию и не перезаписывает экспорт; cookies других доменов
   и SoundCloud не используются. Не экспортировать все cookies обычного браузера через
   `--cookies-from-browser`. Для отключения убрать переменную из `.env` и снова выполнить команду шага 3.

Cookies не гарантируют снятие ограничений YouTube.

## Резервные копии

Каждую ночь Хаб делает копию своей базы на диск сервера (`B:\Music\.backups\hub\`, 7 копий), а если подключён
Google Drive — отправляет её туда же, в папку «HomeMusic backups». В Google Drive уходят база (история
прослушиваний, лайки, плейлисты, теги, тексты, правки), файлы, загруженные вручную, обложки альбомов, поставленные
вручную, и `config.yaml`. Хранятся копии базы за последние 7 дней и по одной за каждую из 4 недель до них; каждый
файл отправляется один раз. Скачанная музыка в копию не входит: после восстановления Хаб скачает её заново. Вход
пользователей в копию не входит: после восстановления все входят заново под прежними логинами.

**`.env` в Google Drive не отправляется.** В нём `HUB_SECRET_KEY` — ключ, которым в базе зашифрованы пароли
пользователей и подключения сервисов. Сохраните копию `.env` у себя (менеджер паролей, флешка) и обновляйте её, если
меняете `.env`. Без неё копия восстановится, но пользователям придётся заново задать пароли.

### Подключение Google Drive (один раз)

1. Откройте [Google Cloud Console](https://console.cloud.google.com/) под своим аккаунтом Google и создайте проект
   (например, «HomeMusic»).
2. «APIs & Services → Library» → найдите **Google Drive API** → **Enable**.
3. «Google Auth Platform» (или «OAuth consent screen»): тип пользователей **External**, название — HomeMusic, ваша
   почта в полях почты. Затем «Audience» → **Publish app** («In production»). Это важно: в режиме «Testing» Google
   отзывает доступ через 7 дней. Проверка приложения Google не нужна — Хаб просит доступ только к своим файлам.
4. «Clients» (или «Credentials») → **Create client** → тип **TVs and Limited Input devices** → **Create**.
   Скопируйте Client ID и Client secret.
5. Добавьте их в `.env` и перезапустите Хаб (`.\scripts\update-server.ps1 -Force`):
   ```
   GOOGLE_DRIVE_CLIENT_ID=…apps.googleusercontent.com
   GOOGLE_DRIVE_CLIENT_SECRET=…
   ```
6. В приложении: «Меню → Резервные копии → Подключить Google Drive». Откроется google.com/device — введите код со
   страницы и разрешите доступ. Первая копия отправится сразу.

На странице «Резервные копии» видно, когда была последняя копия, сколько места занято, и есть «Сделать копию
сейчас». Если копии не отправлялись больше двух суток, в «Меню» рядом с пунктом появится предупреждение. Доступ Хаба
к Drive можно отозвать в аккаунте Google: «Безопасность → Сторонние приложения».

### Восстановление

1. Установите HomeMusic на новом сервере («Запуск с нуля») и положите в корень проекта сохранённый `.env` (с тем же
   `MUSIC_ROOT` или новым — как удобнее).
2. Запустите
   ```
   .\scripts\restore-server.ps1                    # последняя копия
   .\scripts\restore-server.ps1 -Day 2026-11-20    # копия за этот день
   ```
   Скрипт покажет код для google.com/device — войдите в тот же аккаунт Google. Затем он спросит подтверждение,
   заменит базу копией, вернёт загруженные файлы и обложки, поставит остальную музыку на скачивание и запустит Хаб.
3. Пользователи входят под прежними логинами. Музыка докачивается в фоне — ход виден в «Загрузках».

## Полезное

**`HomeMusic.bat`** (ярлык на рабочем столе) включает и выключает HomeMusic одним запуском: если работают все
контейнеры — спрашивает подтверждение, предупреждает о загрузках в очереди и делает `docker compose stop`;
иначе запускает Docker Desktop (если он не запущен), `docker compose up -d` и ждёт ответа Хаба. Docker Desktop
не выключает (в нём могут работать и другие контейнеры). Выключенный так HomeMusic не поднимается сам после перезагрузки ПК.

```
docker compose ps
docker logs --tail 50 homemusic_hub          # JSON-логи Хаба
docker logs --tail 20 homemusic_tunnel       # подключения туннеля (профиль tunnel)
docker compose restart tunnel
curl http://localhost:8080/healthz
```

- Бэкапы на диске (см. «Резервные копии»): база Хаба — архивы PostgreSQL `hub-ГГГГММДД.dump` в `B:\Music\.backups\hub\` (каждый день в 4:00, 7 копий), база Navidrome —
  `B:\Music\.backups\navidrome\` (в 4:30, 7 копий).
- Удалённое из Хаба лежит в `B:\Music\.trash\<дата>\` 30 дней.
- ID треков Navidrome стабильны: Хаб пишет в файлы теги `HOMEMUSIC_TRACK_ID` / `HOMEMUSIC_ALBUM_ID`, из них Navidrome
  строит свои ID (`navidrome/navidrome.toml`). Не меняйте `PID.*` без бэкапа базы Navidrome.
- Источники и лимиты — `config.yaml` (порядок источников для русской и остальной музыки — для русской по умолчанию
  hitmoz → hitmos.fm → SoundCloud → перезаливы YouTube → YouTube Music; `sources.uploaded_before` — файлы SoundCloud,
  загруженные с 2026-05-01, не берутся; источники перезаливов
  без цензуры `sources.reuploads` — YouTube и SoundCloud, домены hitmoz, пороги сопоставления). `sources.download_fallback`
  (по умолчанию `[soundcloud]`, `[]` отключает) — резерв полной записи для загрузки и прослушивания после отказа обычных источников, в том числе
  проверки «не робот» YouTube. Действуют те же проверки исполнителя, версии и длительности. Плеер воспроизводит полные
  записи, а при их недоступности сообщает об ошибке. ИИ-провайдер — `.env` (`LLM_BASE_URL`, `LLM_MODEL`;
  запасной вариант — Ollama `qwen3-vl:8b`).

## Сервис `embedder` (звуковые векторы для миксов)

Нейросеть MuQ-MuLan-large (`embedder/`, контейнер `homemusic_embedder`) превращает фрагмент песни (30 с из середины
файла библиотеки или превью Deezer) и текст в векторы одного пространства; Хаб сравнивает по ним кандидатов миксов
и «Моей волны» со вкусом пользователя. API — [hub/API.md](hub/API.md).

- **Запуск:** `docker compose up -d --build embedder`, затем `docker image prune -f` (прежний образ — ~7 ГБ — иначе остаётся
  без имени). Порт наружу не открыт: Хаб ходит в `http://embedder:8090`
  (`EMBEDDER_URL` в `.env`; `EMBEDDER_TOKEN` — по желанию, тот же в обоих сервисах).
- **Первая загрузка весов:** при первом задании (или `POST /text`) сервис скачивает с Hugging Face ~2,5 ГБ в том
  `homemusic_embedder-models` — это несколько минут; дальше модель грузится из тома прямо на видеокарту за ~12 с
  (пик RAM контейнера ~3,4 ГБ, лимит `mem_limit: 4g`). Без работы 5 минут модель выгружается из памяти
  (`EMBEDDER_IDLE_UNLOAD_S`) — видеокарта общая. Веса подмоделей (`models--OpenMuQ--MuQ-large-msd-iter`,
  `models--xlm-roberta-base`, ~2,4 ГБ) сервису больше не нужны — из них берутся только конфиги и токенизатор.
- **Видеокарта:** нужен Docker Desktop с WSL2 и драйвер NVIDIA на Windows. Проверка, что GPU виден в контейнере:
  ```
  docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi
  ```
  Если на видеокарте меньше 5 ГБ свободно (по `nvidia-smi`, `EMBEDDER_MIN_FREE_GPU_MB`), модель не грузится и ждёт
  карту: в `/healthz` — `state: error` с `GpuBusy`, очередь стоит, попытка раз в минуту; Хаб тем временем строит миксы и
  волну без звука. Принудительно — `EMBEDDER_DEVICE=cuda|cpu`; на CPU модели нужно ~7 ГБ RAM (≈ 5 с на песню) —
  сначала поднять `mem_limit`/`memswap_limit` в `docker-compose.yml`.
- **Состояние:** `docker exec homemusic_embedder python -c "import urllib.request as u; print(u.urlopen('http://localhost:8090/healthz').read().decode())"` — модель, где она (`device`),
  очередь, скорость последней пачки. Замер на файлах библиотеки:
  `docker compose run --rm embedder python -m app.bench --songs 64`.
- Тесты — в образе сервиса (модель в них — фейк):
  ```
  docker run --rm -v "${PWD}\embedder:/src" -w /src homemusic-embedder:local bash -c "uv sync -q --frozen --extra gpu && uv run --no-sync pytest -q -p no:cacheprovider -n 6"
  ```
  С настоящей моделью (медленно): добавить `--gpus all -v homemusic_embedder-models:/models -e EMBEDDER_REAL_MODEL_TESTS=1`
  и `-m gpu` к pytest.

## Разработка

Postgres, Navidrome и youtube-pot работают в Docker (`docker-compose.dev.yml`, отдельный проект `homemusic-dev`
со своими томами: настоящую установку не задевает), Хаб и PWA — на хосте с перезагрузкой при правках.
Нужны Docker, [uv](https://docs.astral.sh/uv/), Node 22, `ffmpeg`, `fpcalc` (`libchromaprint-tools`) и `deno`
(он нужен yt-dlp для YouTube). Python — 3.12 или новее, uv ставит его сам.

```
scripts/dev.sh up       # контейнеры; в первый раз ещё .env.dev, config.dev.yaml и папки data/music
scripts/dev.sh hub      # Хаб на :8080 (uvicorn --reload)
scripts/dev.sh web      # PWA на http://localhost:5173, /api проксируется на :8080
scripts/dev.sh test     # тесты Хаба (аргументы уходят в pytest: scripts/dev.sh test -k tags)
scripts/dev.sh scan     # пересканировать data/music/library в Navidrome
scripts/dev.sh down     # остановить контейнеры (down -v — ещё и стереть их данные)
```

Вход — `admin` / `admin` (этого админа Navidrome создаёт при первом старте). Музыку для опытов кладут в
`data/music/library` или загружают через Хаб. Настройки Хаба — `.env.dev` (любое поле `Settings` из
`hub/app/config.py`), источники и пороги — `config.dev.yaml`; оба файла вне git. Embedder локально не поднимается:
миксы и волна строятся без звука.

Тесты создают и удаляют свои временные базы на том же Postgres (`TEST_DATABASE_URL` в `.env.dev`).
Только фронт, без бэкенда: `cd web && npm run dev:mock` — Vite сам отвечает фейковым API.
Тесты embedder (модель в них — фейк): `cd embedder && uv run pytest -q -n auto`.

## Откат шлюза на VPS

```
ssh myvps
systemctl disable --now homemusic-tunnel-watchdog homemusic-guard-firewall
rm -f /etc/systemd/system/homemusic-tunnel-watchdog.service /etc/systemd/system/homemusic-guard-firewall.service /usr/local/sbin/homemusic-tunnel-watchdog
rm -rf /etc/homemusic; nft delete table inet homemusic_guard 2>/dev/null; systemctl daemon-reload
rm -f /etc/ssh/sshd_config.d/61-homemusic-tunnel.conf && sshd -t && systemctl reload ssh
pkill -u homemusic-tunnel; userdel -r homemusic-tunnel
# в /etc/caddy/Caddyfile удалить блок между "# >>> homemusic" и "# <<< homemusic", затем: systemctl reload caddy
```
