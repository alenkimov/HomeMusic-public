# Карточки треков: план работ

Цель: любой список треков (лайки, плейлисты, «Для вас», поиск по библиотеке) отдаётся за ~0,1–0,2 с и не
нагружает Хаб, вместо сборки каждого трека в Python на каждом запросе.

## Как устроено (сделано)

- `hub/app/library/cards.py`:
  - `draw(s, rows)` — единственный способ нарисовать трек (бывший конвейер `tracks_out`);
  - `build(ids)` — рисует и сохраняет карточки в `track_cards` (своя сессия, защита от гонок через `gen`);
  - `read(s, ids)` — готовые карточки, устаревшие/отсутствующие/просроченные дорисовывает сразу;
  - `ensure(s, ids)` — то же без чтения карточек;
  - `list_json(s, playlist_id, user_id, extra=)` — JSON-массив плейлиста собирает Postgres
    (`json_agg` по `playlist_items` ⨝ `track_cards`) + надстройка пользователя (`added_at`,
    `import_review`, `unavailable` из `imports.review.word`);
  - `keep_fresh()` — фоновый цикл (запущен в `main.py` lifespan): пересборка грязных и сверка с индексом
    песен Navidrome при каждом его перечитывании (`songs.loaded_at`).
- Миграция `hub/migrations/versions/30a000000004_track_cards.py`: таблица, GIN-индекс по `deps`, триггеры на
  tracks (только нужные столбцы), track_files, track_covers, albums, download_items (журнал), import_items,
  song_tracks, artist_names, artist_refs, artists, catalog_cache (`listen:*`), catalog_release_matches.
  Токены `deps`: `k:` ключ имени артиста, `r:` каталожный ref артиста, `a:` id артиста, `c:` ключ проверки
  записи, `g:` album_ref для групп релизов.
- Пользовательское (не в карточке): `import_review`, `unavailable |= перенос не нашёл`, `added_at` списка.
- `tracks_out` (api/playlists.py) читает карточки; `GET /api/nd/liked` и `GET /api/playlists/{id}` отдают JSON
  из `list_json` в обход pydantic.
- Тесты: `hub/tests/test_cards.py`. Эквивалентность на живых данных проверялась скриптом: новый ответ против
  `tracks_out` + `added_at`, сравнение с нормализацией дат — 0 расхождений.

## Оставшиеся пункты

1. [ ] **ETag/304 для списков.** В `list_json` разделить подготовку (ensure + word) и сборку; версия списка =
   md5 от SQL-агрегата `(pi.track_id, pi.position, pi.added_at, c.built_at)` по плейлисту + надстройки
   (`said`, `failed`, `extra`, для лайков ещё albums/artists). В эндпоинтах: заголовки
   `ETag` и `Cache-Control: private, no-cache`; при `If-None-Match` == версии → `Response(status_code=304)`.
   Браузер сам шлёт `If-None-Match`, service worker (`web/public/sw.js`, networkFirst) это не ломает.
2. [ ] **Облегчённая модель списка.** Сейчас 42 поля на трек (~1,2 КБ). Найти поля, которые клиент читает в
   строке списка (`web/src/components/catalog.tsx` TrackRow, `lib/listen.ts` ListenTrack, меню трека), и
   отдавать в списках только их; детали — по запросу. Осторожно: `ListenTrack` сохраняется в очередь плеера.
3. [ ] **Точечные обновления (этап 4).** События Хаба (`bus.publish`) несут id изменившихся треков;
   эндпоинт `GET /api/tracks/cards?ids=` отдаёт карточки; клиент держит общее хранилище id → карточка и
   патчит списки на месте вместо перечитывания (`useHubEvent('library', ...)` в pages/MediaLibrary.tsx).
4. [ ] **Остальные списки (этап 5).** Перевести на карточки `songs_out` в api/medialib.py (альбом Navidrome,
   все песни, «чужие» звёзды), search, history, for_you (они уже через `tracks_out`, но можно `list_json`-подход
   для больших); затем удалить `songs_out`.

## Как проверять

- Тесты Хаба: временный Postgres (`docker run postgres:16-alpine` в сети `hm-test`) и
  `docker run ... homemusic-hub:local` с `TEST_DATABASE_URL=postgresql://postgres:test@hm-test-pg:5432/postgres`,
  `uv pip install pytest pytest-asyncio pytest-xdist respx`, `pytest -n 6 tests`.
- Деплой: `docker compose up -d --build hub` (миграции накатываются при старте).
- Замеры на живых данных: скрипт в контейнере с `httpx.ASGITransport` и `app.dependency_overrides[current_user]`.
