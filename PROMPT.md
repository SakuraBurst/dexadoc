Да. Ниже — ТЗ, которое уже можно отдавать Codex почти без переписывания.

База форка: берите стабильный тег `paperless-ngx` `v2.20.13`, а не плавающий `dev`. Upstream-доки пишут, что `main` соответствует последнему release, `dev` — следующему, а код разделён на `src` и `src-ui`. ([GitHub][1])

Почему это должен быть именно форк, а не “плагин” или hook: у `paperless-ngx` нет полноценной plugin system; официально поддерживаемое расширение — это custom parsers через `document_consumer_declaration`, то есть расширение parser-слоя. Pre/post consume scripts тоже не подходят: pre-script работает с `DOCUMENT_WORKING_PATH`, post-script не может отменить consume и не должен модифицировать document files напрямую. ([Paperless-ngx][2])

Текущие точки, которые реально придётся менять: `DocumentSource` и `ConsumableDocument` в `src/documents/data_models.py`; consume task и plugin chain в `src/documents/tasks.py`; интерфейс `ConsumeTaskPlugin`; хранение оригинала и checksum-dedup в `src/documents/consumer.py`; локальные `source_path` / `archive_path` / `thumbnail_path` в `src/documents/models.py`; физическое move/delete файлов в `src/documents/signals/handlers.py`; поиск и индексные поля в `src/documents/index.py`; reprocess через `document.source_path`; sanity checker; exporter; а также API/UI для preview и карточки документа. Сейчас `consume_file` собирает plugin chain, `ConsumerPreflightPlugin` проверяет дубликаты по checksum, `ConsumerPlugin` пишет оригинал в storage и удаляет входной файл, `Document.source_path` строится из `ORIGINALS_DIR`, сигналы двигают и удаляют файлы физически, sanity checker валидирует original/archive/thumbnail на диске, exporter копирует `document.source_path`/`thumbnail_path`/`archive_path`, а индекс хранит `viewer_id` и логический `storage_path`, а не путь на файловой шаре. ([GitHub][3])

Отдельно: не менять search backend в v1. Maintainер upstream прямо писал, что замена search backend “not simple” и это “huge amount(s) of work”, а текущий индекс использует Whoosh. ([GitHub][4])

А parity с Fess надо понимать правильно: Fess умеет crawl файловых систем и Windows shared folders, настраивает `smb://...`, include/exclude regex, file authentication, а при интеграции с AD может учитывать permission information shared folder’а и фильтровать результаты по правам пользователя. Это отдельный слой и его не надо пытаться засунуть в первую итерацию `dexadoc`. ([Fess][5])

---

# ТЗ для Codex: форк `dexadoc`

## 1. Цель

Сделать форк `paperless-ngx` под названием `dexadoc`, который поддерживает два режима:

* `managed` — текущее поведение upstream без изменений;
* `external` — режим reference/search-only, где оригинальный файл остаётся на внешнем read-only filesystem mount, а локально хранятся только:

  * OCR/извлечённый текст в БД,
  * thumbnail,
  * опциональный preview/archive cache.

Система должна индексировать документы с внешней SMB-шары, смонтированной на Linux host и проброшенной в контейнер read-only. `dexadoc` в v1 не должен сам говорить по SMB из Django-кода.

## 2. Не-цели v1

Не делать в этой задаче:

* нативный SMB client внутри Django;
* отражение Windows ACL / AD group membership в поиске;
* замену Whoosh/SQL поиска на OpenSearch/Elastic/Manticore;
* запись, удаление, rename или move файлов на внешней шаре;
* реализацию через pre/post consume hooks;
* перенос crawler-кода Fess на Python один-в-один.

## 3. Архитектурное решение

`dexadoc` должен получить новый доменный режим: **external reference storage**.

Ключевая идея:

* ingest/crawler берёт файл с mounted share;
* создаёт scratch-копию во временной директории;
* парсинг/OCR работают по scratch-копии;
* после индексации scratch удаляется;
* в `Document` сохраняется ссылка на внешний файл;
* preview/download читают файл по внешнему пути;
* локальные артефакты ограничиваются thumbnail и, по policy, preview/archive cache.

### Принципы

* оригинал внешнего документа никогда не копируется в `media/originals`;
* логика upstream для managed-документов не ломается;
* уникальность внешнего документа — по `(external_source, external_relpath)`, а не по checksum;
* одинаковые файлы в разных папках должны индексироваться как разные документы;
* изменение файла по тому же пути должно обновлять тот же `Document`, а не создавать дубликат;
* исчезновение файла на шаре должно помечать документ как `source unavailable`, а не удалять его автоматически.

## 4. Модель данных

### 4.1. Изменения в `src/documents/models.py`

Добавить:

```python
class StorageBackend(models.TextChoices):
    MANAGED = "managed", _("managed")
    EXTERNAL = "external", _("external reference")
```

В `Document` добавить поля:

```python
storage_backend = models.CharField(
    max_length=16,
    choices=StorageBackend.choices,
    default=StorageBackend.MANAGED,
    db_index=True,
)

external_source = models.ForeignKey(
    "external_sources.ExternalSource",
    null=True,
    blank=True,
    on_delete=models.SET_NULL,
    related_name="documents",
)

external_relpath = models.TextField(null=True, blank=True)
external_mtime_ns = models.BigIntegerField(null=True, blank=True)
external_size = models.BigIntegerField(null=True, blank=True)
source_available = models.BooleanField(default=True, db_index=True)
last_seen_at = models.DateTimeField(null=True, blank=True)
external_last_error = models.TextField(blank=True, default="")
```

Изменить поле `checksum` (сейчас `unique=True`): убрать `unique=True`, оставить `db_index=True`. Без этого невозможно выполнить требование 8.4 (одинаковый checksum в разных path = два Document).

Добавить constraints в `class Meta`:

```python
# Partial unique: checksum уникален только для managed docs
models.UniqueConstraint(
    fields=["checksum"],
    condition=models.Q(storage_backend="managed"),
    name="documents_document_managed_checksum_uniq",
),

# Partial unique: (source, relpath) уникален для external docs
models.UniqueConstraint(
    fields=["external_source", "external_relpath"],
    condition=models.Q(
        storage_backend="external",
        deleted_at__isnull=True,
    ),
    name="documents_document_external_source_relpath_uniq",
),
```

### 4.2. Новые computed properties

В `Document` добавить:

* `is_external`
* `display_source_path`
* backend-aware `source_path`

Логика `source_path`:

* Проверка `is_external` должна идти ПЕРВОЙ, до fallback на `filename` / `{pk:07}{file_type}` (upstream fallback указывает на `ORIGINALS_DIR`, что неверно для external docs);
* `managed` → текущее upstream-поведение;
* `external` → `external_source.mount_root / external_relpath`, с обязательной нормализацией и защитой от path traversal.

Проверка обязательна:

* `resolve()`
* путь должен оставаться внутри `mount_root`
* `../` или абсолютный relpath должны отклоняться ошибкой.

`archive_path` и `thumbnail_path` оставить локальными.

## 5. Новый app для внешних источников

Создать новый Django app: `src/external_sources/`

### 5.1. Модель `ExternalSource`

```python
class ExternalSource(models.Model):
    code = models.SlugField(unique=True)
    name = models.CharField(max_length=128)
    mount_root = models.TextField()      # путь внутри контейнера
    display_root = models.TextField()    # например \\FILESRV\Docs
    enabled = models.BooleanField(default=True)
    recursive = models.BooleanField(default=True)
    follow_symlinks = models.BooleanField(default=False)
    include_regex = models.TextField(blank=True, default="")
    exclude_regex = models.TextField(blank=True, default="")
    max_depth = models.PositiveIntegerField(null=True, blank=True)
    max_file_size_mb = models.PositiveIntegerField(null=True, blank=True)
    scan_interval_minutes = models.PositiveIntegerField(null=True, blank=True)

    last_scan_started_at = models.DateTimeField(null=True, blank=True)
    last_scan_finished_at = models.DateTimeField(null=True, blank=True)
    last_scan_status = models.CharField(max_length=32, blank=True, default="")
    last_scan_message = models.TextField(blank=True, default="")
```

### 5.2. Модель `ExternalSourceScan`

```python
class ExternalSourceScan(models.Model):
    source = models.ForeignKey(ExternalSource, on_delete=models.CASCADE, related_name="runs")
    mode = models.CharField(max_length=16, choices=[("full", "full"), ("delta", "delta")])
    status = models.CharField(max_length=16, choices=[("running","running"),("success","success"),("failed","failed")])
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)

    seen_count = models.PositiveIntegerField(default=0)
    queued_count = models.PositiveIntegerField(default=0)
    updated_count = models.PositiveIntegerField(default=0)
    unchanged_count = models.PositiveIntegerField(default=0)
    missing_count = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)

    message = models.TextField(blank=True, default="")
```

## 6. Ingest / data model для consume

В `src/documents/data_models.py` сейчас есть `DocumentSource` и `ConsumableDocument`; `DocumentSource` содержит четыре значения, а `ConsumableDocument` уже имеет `original_file` и `original_path`. Это надо расширить, а не обходить сторонним объектом. ([GitHub][3])

Добавить:

```python
class DocumentSource(IntEnum):
    ConsumeFolder = 1
    ApiUpload = 2
    MailFetch = 3
    WebUI = 4
    Crawler = 5
```

Расширить `ConsumableDocument`:

```python
external_source_id: int | None = None
external_relpath: Path | None = None
source_stat_mtime_ns: int | None = None
source_stat_size: int | None = None
```

### Важное правило

Для `DocumentSource.Crawler`:

* `original_file` = scratch-копия;
* `original_path` = реальный путь внешнего файла на mounted share.

Так `ConsumerPlugin` сможет безопасно удалить `original_file`, не трогая оригинал.

## 7. Изменения в consume pipeline

`consume_file` уже строит plugin chain через `ConsumeTaskPlugin`; именно туда и надо встраиваться, а не monkeypatch’ить случайные функции. ([GitHub][6])

### 7.1. `src/documents/tasks.py`

Добавить новую ветку plugin chain:

```python
if input_doc.source == DocumentSource.Crawler:
    plugins = [
        ReferencePreflightPlugin,
        ReferenceConsumerPlugin,
    ]
else:
    plugins = [
        ConsumerPreflightPlugin,
        CollatePlugin,
        BarcodePlugin,
        WorkflowTriggerPlugin,
        ConsumerPlugin,
    ]
```

Для crawler-mode не запускать:

* ASN logic,
* workflows,
* collate/barcode.

Это не нужно в search-only режиме и только увеличит сложность.

### 7.2. `src/documents/consumer.py`

Сейчас preflight duplicate check считает checksum и ищет `Document` по `checksum` / `archive_checksum`; при включённом `CONSUMER_DELETE_DUPLICATES` он даже удаляет входной файл. Для search-over-share это неверная семантика, потому что одинаковый файл в двух папках — это два разных результата поиска. ([GitHub][7])

#### Добавить `ReferencePreflightPlugin`

Поведение:

* проверить, что scratch-файл существует;
* проверить, что указан `external_source_id` и `external_relpath`;
* создать scratch/thumb/archive dirs при необходимости;
* **не делать duplicate-check по checksum**.

#### Добавить `ReferenceConsumerPlugin`

Это fork текущего `ConsumerPlugin` с такими изменениями:

1. Парсинг/OCR — оставить как у upstream.
2. `_store()` переписать:

   * искать существующий `Document` по `(external_source_id, external_relpath)`;
   * если найден и файл не изменился — пропустить reindex;
   * если найден и изменился — обновить существующий `Document`;
   * если не найден — создать новый.
3. Для external docs:

   * `storage_backend = EXTERNAL`
   * `filename = None`
   * оригинал не писать в `document.source_path`
   * thumbnail писать локально
   * `archive_filename` задавать только если локальный preview cache реально нужен
4. После успеха:

   * удалить только scratch `original_file` и `working_copy`
   * внешний `original_path` не трогать

### 7.3. Политика archive/preview cache

Чтобы не получить “почти Paperless по объёму”, для external docs нужна политика:

* PDF / image / text previewable форматы:

  * по умолчанию **не сохранять** локальный archive PDF/A;
* Office и иные форматы, где preview без конвертации плохой:

  * если parser вернул `archive_path`, сохранить его как preview cache;
* отдельная настройка:

  * `DEXADOC_EXTERNAL_STORE_ARCHIVE_CACHE=false` по умолчанию.

## 8. Правила уникальности и изменения файлов

Это отдельный блок, который Codex должен соблюсти буквально.

### 8.1. New file

Если `(external_source, external_relpath)` не найден:

* создать новый `Document`.

### 8.2. Same path, same file

Если path совпадает, а `(mtime_ns, size)` не изменились:

* не OCR’ить заново;
* обновить `last_seen_at`, `source_available=True`.

### 8.3. Same path, changed file

Если path совпадает, а `(mtime_ns, size)` изменились:

* переиндексировать;
* обновить тот же `Document`.

### 8.4. Different path, same checksum

Если checksum совпадает, но путь другой:

* создать второй `Document`;
* не считать дубликатом.

### 8.5. Missing file

Если документ в БД есть, но на crawl-run путь не встретился:

* `source_available=False`
* `external_last_error="missing"`
* не удалять документ автоматически в v1

### 8.6. Rename/move on share

В v1 трактовать как:

* old path → missing
* new path → new document

## 9. Crawler

Создать:

* `src/external_sources/services.py`
* `src/external_sources/tasks.py`
* `src/external_sources/management/commands/dexadoc_scan_source.py`
* `src/external_sources/management/commands/dexadoc_reconcile_sources.py`
* `src/external_sources/management/commands/dexadoc_purge_unavailable.py`

### 9.1. Алгоритм scan

1. Взять `ExternalSource.mount_root`
2. `os.walk(...)`
3. Учитывать:

   * `recursive`
   * `follow_symlinks`
   * `max_depth`
   * include/exclude regex
   * `max_file_size_mb`
4. Игнорировать по умолчанию:

   * `~$*`
   * `._*`
   * `Thumbs.db`
   * `desktop.ini`
5. Для каждого файла:

   * получить `st_size`, `st_mtime_ns`
   * вычислить `relpath`
   * сравнить с имеющимся `Document`
   * при new/changed:

     * скопировать файл в scratch
     * создать `ConsumableDocument(source=Crawler, original_file=scratch, original_path=real_path, ...)`
     * отправить в `consume_file.delay(...)`

### 9.2. Celery beat

Добавить periodic task:

* `scan_enabled_external_sources`

Поведение:

* проход по всем `enabled=True` источникам;
* один scan run на source;
* ошибки одного source не останавливают остальные.

## 10. Signals

В `src/documents/signals/handlers.py` сейчас есть как минимум два обязательных узла: удаление документа физически чистит original/archive/thumbnail, а `update_filename_and_move_files` реально двигает файлы по storage roots. Для external docs это надо жёстко развести по веткам. ([GitHub][8])

### 10.1. `cleanup_document_deletion`

* `managed` → старое поведение
* `external` → удалить только:

  * local thumbnail
  * local archive/preview cache
* оригинал на внешней шаре не трогать

### 10.2. `update_filename_and_move_files`

* `managed` → старое поведение
* `external` → no-op для physical move
* metadata save допускается
* нельзя пытаться валидировать external path against `ORIGINALS_DIR`

## 11. Search index

Сейчас индекс хранит `viewer_id`, `path`, `path_id`, `has_path`; при этом `path` строится из `doc.storage_path.name`, то есть это логическая storage category, а не filesystem path. Это трогать нельзя; для внешнего пути нужны новые поля. ([GitHub][9])

В `src/documents/index.py` добавить в schema:

```python
storage_backend=KEYWORD(),
is_external=BOOLEAN(),
external_source=TEXT(sortable=True),
external_source_id=NUMERIC(),
external_relpath=TEXT(sortable=True),
external_dir=TEXT(sortable=True),
external_basename=TEXT(sortable=True),
display_path=TEXT(sortable=True),
source_available=BOOLEAN(),
acl_principal_id=KEYWORD(commas=True),  # placeholder for v2
```

При обновлении индекса заполнять:

* `storage_backend`
* `is_external`
* `external_source`
* `external_source_id`
* `external_relpath`
* `external_dir`
* `external_basename`
* `display_path`
* `source_available`

### Правило

Не переиспользовать текущее поле `path` под реальный путь на шаре.

## 12. Reprocess

`update_document_content_maybe_archive_file(document_id)` уже берёт parser по `document.source_path`. Это хороший крючок: как только `source_path` станет backend-aware, reprocess начнёт работать и для external docs. Но функцию надо проверить на запись archive и thumbnail, чтобы она не пыталась сохранять original локально. ([GitHub][6])

Требование:

* `managed` → без изменений
* `external` → reprocess берёт внешний `source_path`, обновляет text/thumb/archive cache, но не пишет original в media

## 13. API и serializers

Расширить `src/documents/serialisers.py` и `src/documents/views.py`.

### 13.1. В сериализатор документа добавить read-only поля

* `storage_backend`
* `is_external`
* `external_source`
* `external_relpath`
* `display_source_path`
* `source_available`
* `last_seen_at`

### 13.2. Фильтры API

Добавить фильтрацию по:

* `storage_backend`
* `source_available`
* `external_source`

### 13.3. Новые действия

Для external docs добавить:

* `unindex` — удалить запись из `dexadoc` без удаления файла на шаре
* `copy_path` — либо отдельное action, либо просто поле `display_source_path`

### 13.4. Delete semantics

Обычный `DELETE` для external docs лучше **запретить** `409 Conflict` с сообщением:

* “For external documents use unindex.”

Это безопаснее, чем молча подменять семантику delete.

### 13.5. Preview/download

Документированные endpoints `thumb` и `preview` уже существуют в API. Для external docs они должны продолжить работать через backend-aware `source_path` и локальный preview cache, если он есть. ([GitHub][10])

## 14. Sanity checker

Сейчас sanity checker отдельно проверяет thumbnail, original и archive: отсутствие original считается ошибкой, checksum сверяется с `doc.checksum`, отсутствие archive тоже считается ошибкой, если он должен быть. Для external docs это надо менять семантически. ([GitHub][11])

Требования:

* `managed` → текущее поведение
* `external`:

  * thumbnail проверять как сейчас
  * original проверять как availability check
  * missing external original = warning/status unavailable, а не corruption
  * checksum mismatch при доступном файле = error с рекомендацией reindex
  * archive проверять только если есть локальный preview cache

## 15. Exporter

Сейчас exporter копирует `document.source_path`, `document.thumbnail_path` и `document.archive_path`; в upstream-сообществе отдельно жаловались, что missing original ломает export. Поэтому для `dexadoc` нужен отдельный режим экспорта ссылок. ([GitHub][12])

Добавить в `src/documents/management/commands/document_exporter.py` флаги:

* `--refs-only`
* `--with-external-files`

### Поведение

* managed docs → как upstream
* external docs:

  * `--refs-only` → экспорт metadata + refs + thumbs + preview cache
  * `--with-external-files` → дополнительно попытка скопировать originals с mounted share

В manifest для external docs сохранять:

* `storage_backend`
* `external_source.code`
* `external_source.name`
* `external_relpath`
* `display_source_path`
* `source_available`
* `external_size`
* `external_mtime_ns`

## 16. Front-end

Upstream front-end живёт в `src-ui`, а тестируется через Jest и Playwright; UI в `dexadoc` надо менять минимально, не строя новый фронтенд. ([Paperless-ngx][2])

### Что добавить

* badge `External`
* индикатор `Available / Unavailable`
* отображение внешнего пути
* кнопка `Скопировать путь`
* кнопка `Убрать из индекса`
* фильтры:

  * `Managed / External`
  * `Available / Unavailable`
  * `Source`

### Что убрать/скрыть для external docs

* destructive delete как будто это удаление оригинала
* любые формулировки, будто файл “хранится в dexadoc”

## 17. Настройки

Добавить новые конфиги:

```env
DEXADOC_ENABLE_EXTERNAL_SOURCES=true
DEXADOC_EXTERNAL_STORE_ARCHIVE_CACHE=false
DEXADOC_EXTERNAL_HIDE_UNAVAILABLE_BY_DEFAULT=true
DEXADOC_EXTERNAL_DISABLE_CHECKSUM_DEDUP=true
DEXADOC_EXTERNAL_ALLOW_UNINDEX=true
DEXADOC_EXTERNAL_MAX_SCAN_WORKERS=2
DEXADOC_EXTERNAL_IGNORE_PATTERNS=~$*,._*,Thumbs.db,desktop.ini
```

И описать их в example config.

## 18. Phase 2: ACL/AD parity

Это **не делать в v1**, но оставить extension points.

Нужный later design:

* crawler собирает ACL/principal snapshot;
* identity mapping через LDAP/AD;
* индекс хранит `acl_principal_id`;
* search filter мапит logged-in user → allowed principals.

Это отдельный epic, потому что именно так Fess добивается per-user search results по правам shared folder и AD. ([Fess][5])

## 19. Тесты

Обязательные backend tests:

1. `source_path` external docs не даёт выйти за `mount_root`
2. crawler-mode не пишет original в `media/originals`
3. одинаковый checksum в двух разных external paths создаёт два `Document`
4. same path + changed mtime/size обновляет тот же `Document`
5. missing file помечается `source_available=False`
6. `unindex` не удаляет внешний файл
7. `update_filename_and_move_files` no-op для external docs
8. `cleanup_document_deletion` не трогает внешний original
9. sanity checker не считает missing external original “сломанным media”
10. exporter `--refs-only` проходит без external original copy
11. managed upload/consume остаётся совместимым с upstream

Обязательные UI/e2e tests:

1. badge `External`
2. показ внешнего пути
3. copy path
4. unindex action
5. фильтры по backend/source availability
6. preview/download внешнего PDF при доступном source

## 20. Definition of done

Работа считается завершённой, если:

* `dexadoc` индексирует mounted share без копирования originals в `media/originals`;
* документы ищутся по содержимому;
* preview/download работают для доступных external docs;
* unavailable docs корректно помечаются;
* managed mode не сломан;
* миграции проходят;
* тесты проходят.

---

Deliver in small commits in this order:
1. models + migrations
2. backend-aware source_path
3. crawler ingest datamodel
4. reference consume plugins
5. signals branching
6. crawler service/tasks/commands
7. index changes
8. views/serializers/API
9. sanity/exporter
10. UI
11. tests/docs
```

Могу следующим сообщением разложить это в GitHub issues / epics / milestones под разработку.

[1]: https://github.com/paperless-ngx/paperless-ngx/releases "https://github.com/paperless-ngx/paperless-ngx/releases"
[2]: https://docs.paperless-ngx.com/development/ "https://docs.paperless-ngx.com/development/"
[3]: https://github.com/paperless-ngx/paperless-ngx/blob/dev/src/documents/data_models.py "https://github.com/paperless-ngx/paperless-ngx/blob/dev/src/documents/data_models.py"
[4]: https://github.com/paperless-ngx/paperless-ngx/discussions/9649 "https://github.com/paperless-ngx/paperless-ngx/discussions/9649"
[5]: https://fess.codelibs.org/articles/15/document.html "https://fess.codelibs.org/articles/15/document.html"
[6]: https://github.com/paperless-ngx/paperless-ngx/blob/dev/src/documents/tasks.py "https://github.com/paperless-ngx/paperless-ngx/blob/dev/src/documents/tasks.py"
[7]: https://github.com/paperless-ngx/paperless-ngx/blob/dev/src/documents/consumer.py "https://github.com/paperless-ngx/paperless-ngx/blob/dev/src/documents/consumer.py"
[8]: https://github.com/paperless-ngx/paperless-ngx/blob/dev/src/documents/signals/handlers.py "https://github.com/paperless-ngx/paperless-ngx/blob/dev/src/documents/signals/handlers.py"
[9]: https://github.com/paperless-ngx/paperless-ngx/blob/dev/src/documents/index.py "https://github.com/paperless-ngx/paperless-ngx/blob/dev/src/documents/index.py"
[10]: https://github.com/paperless-ngx/paperless-ngx/blob/dev/src/documents/views.py "https://github.com/paperless-ngx/paperless-ngx/blob/dev/src/documents/views.py"
[11]: https://github.com/paperless-ngx/paperless-ngx/blob/dev/src/documents/sanity_checker.py "https://github.com/paperless-ngx/paperless-ngx/blob/dev/src/documents/sanity_checker.py"
[12]: https://github.com/paperless-ngx/paperless-ngx/blob/dev/src/documents/management/commands/document_exporter.py "https://github.com/paperless-ngx/paperless-ngx/blob/dev/src/documents/management/commands/document_exporter.py"
