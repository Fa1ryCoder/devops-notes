# Разбор инцидента: OOM-коллапс сервера и хэширование паролей

**Дата:** 27 апреля 2026  
**Сервер:** fa1ry  
**Затронутые системы:** 14 Java-микросервисов Artixcs, MongoDB

---

## Часть 1. DevOps — OOM-коллапс и управление Java-сервисами

### 1.1 Что случилось

После перезагрузки сервера все 14 Java-микросервисов поднялись одновременно без каких-либо ограничений памяти. JVM по умолчанию берёт до 25% физической RAM на heap — это означало, что каждый сервис мог занять до ~2.8 GB из 11 GB доступной памяти. Суммарно система была обречена.

**Итог за первые 11 минут:**

| Показатель | Значение | Норма |
|---|---|---|
| RAM свободно | 111 MB из 11 GB | > 1 GB |
| Swap свободно | 0 MB из 4 GB | > 1 GB |
| Load average | 234 / 165 / 82 | ≤ кол-ва CPU |
| CPU idle | 0% | > 50% |
| CPU системный (sy) | 81% | < 10% |
| kswapd0 (демон свопа) | 44% CPU | < 1% |

**Что означают эти показатели:**

**RAM на heap** — когда Java-приложение запускается, оно резервирует под свою работу кусок оперативной памяти, который называется heap (куча). Именно здесь JVM хранит все объекты приложения во время работы. По умолчанию JVM берёт под heap до 25% всей физической RAM — без явного ограничения она просто возьмёт столько, сколько считает нужным.

**CPU idle (простой процессора)** — это процент времени когда процессор буквально ничего не делает и ждёт задач. В норме этот показатель должен быть высоким (50%+) — значит у системы есть запас. Когда idle = 0%, процессор перегружен полностью и не справляется с очередью задач.

**CPU системный (sy)** — время которое процессор тратит на системные задачи ядра Linux, а не на код приложений. В норме это единицы процентов. Когда sy = 81% — это значит что ядро занято обслуживанием самой системы (в нашем случае непрерывным перекладыванием данных между RAM и диском), а не реальной работой.

**kswapd0** — это процесс ядра Linux который отвечает за своп. Своп — это когда данные из оперативной памяти выгружаются на диск чтобы освободить место для других процессов. kswapd0 в норме практически не виден. Когда он потребляет 44% CPU — это сигнал что памяти критически не хватает и ядро непрерывно и лихорадочно перемещает данные между RAM и диском.

---

### 1.2 Диагностика

#### Шаг 1 — смотрим кто жрёт память

```bash
ps aux --sort=-%mem | head -20
```

Флаг `-%mem` означает сортировку **по убыванию** — самые жирные процессы сверху. Без минуса — по возрастанию, что бесполезно.

Вывод показал топ потребителей:

```
artixcs-rest          1.6 GB   13.1%
artixcs-datatransfer  1.17 GB   9.6%
artixcs-accounting-coupons 1.05 GB  8.6%
artixcs-online-card   987 MB    8.1%
artixcs-accounting-bonuses 930 MB  7.6%
```

#### Шаг 2 — подтверждаем через top

```bash
top
```

Ключевые сигналы:
- `kswapd0` на 44% CPU — ядро непрерывно перекладывает данные между RAM и диском
- `81% sy` — почти всё время CPU уходит на системные вызовы, а не на работу приложений
- `0% id` (idle) — процессор не отдыхает ни секунды
- Load average 234 — в очереди на CPU стоят сотни процессов одновременно

#### Шаг 3 — понять механизм (Death Spiral)

Система попала в порочный круг:

```
RAM кончилась
      ↓
ядро начинает своповать на диск
      ↓
своп тоже кончился
      ↓
kswapd0 молотит на 44% CPU
      ↓
всё замедляется, очередь задач растёт
      ↓
load average улетает до 234
      ↓
система перестаёт отвечать
      ↓
памяти не освобождается, круг замкнулся
```

Выйти из этого состояния система самостоятельно не может — только ручное вмешательство.

---

### 1.3 Экстренное устранение (тушим пожар)

Первый приоритет — освободить память любой ценой. Останавливаем самых жирных потребителей:

```bash
systemctl stop artixcs-accounting-bonuses-certificates
systemctl stop artixcs-accounting-coupons
systemctl stop artixcs-accounting-scheduled-impacts
systemctl stop artixcs-sales-loader
systemctl stop artixcs-sales-ws
systemctl stop artixcs-report
```

**Результат сразу после:**

| Показатель | До | После |
|---|---|---|
| RAM свободно | 111 MB | 3.1 GB |
| Swap | 4 GB (100%) | 1.5 GB из 4 GB |
| Load average | 234 | 1.83 |
| CPU idle | 0% | 82% |

---

### 1.4 Корневая причина — отсутствие лимитов памяти

#### Что такое JVM и Spring Boot (кратко)

**JVM (Java Virtual Machine)** — это программа которая запускает Java-приложения. Когда ты пишешь `java -jar приложение.jar` — ты запускаешь именно JVM, которая уже внутри себя выполняет код приложения. JVM управляет памятью приложения, и именно ей нужно указывать лимиты через `-Xmx`.

**Spring Boot** — это популярный Java-фреймворк для создания веб-сервисов. Большинство сервисов Artixcs написаны на нём. Одна из его особенностей — он умеет паковать приложение в так называемый **executable JAR**: один файл который содержит и само приложение, и все его зависимости, и даже встроенный веб-сервер (Tomcat). Такой JAR можно запустить прямо как скрипт — именно так и были настроены все юниты изначально.

**Maven и maven.repo.local** — Maven это инструмент сборки Java-приложений. Он управляет зависимостями: библиотеками которые нужны приложению для работы. Эти библиотеки хранятся в так называемом репозитории — папке с JAR-файлами. Параметр `-Dmaven.repo.local=/opt/artixcs-rest/lib/repository` говорит приложению: "все нужные библиотеки ищи вот в этой папке, никуда в интернет не ходи".

**Thin-launcher** — это особый способ запуска Spring Boot приложений. Обычный executable JAR содержит все зависимости внутри себя и весит много. Thin-launcher делает JAR маленьким — зависимости хранятся отдельно в папке `lib/repository`, и при запуске thin-launcher их подгружает. Именно поэтому часть сервисов падала без `-Dmaven.repo.local` — thin-launcher не знал где искать библиотеки и пытался скачать их из интернета.

#### Как сервисы запускались раньше

Spring Boot умеет паковать приложение в **executable JAR** — это одновременно и bash-скрипт, и архив. Именно так были настроены все юниты:

```ini
[Service]
ExecStart=/opt/artixcs-rest/artixcs-rest.jar
```

При таком запуске:
- systemd запускает `/bin/bash artixcs-rest.jar`
- bash читает шапку файла и сам вызывает JVM
- **параметры которые ты пишешь в ExecStart — JVM не получает**
- JVM стартует без `-Xmx` и берёт до 25% RAM по умолчанию

В CGroup это выглядело как два процесса:
```
├─19607 /bin/bash /opt/artixcs-rest/artixcs-rest.jar  ← обёртка
└─19671 /opt/java-artix/bin/java -jar ...              ← реальная JVM, без Xmx
```

#### Что нужно было

Запускать JVM **напрямую**, передавая все параметры явно:

```ini
[Service]
ExecStart=/opt/java-artix/bin/java \
  -Xms256m -Xmx768m \
  -Dsun.misc.URLClassPath.disableJarChecking=true \
  -Dmaven.repo.local=/opt/artixcs-rest/lib/repository \
  -Dfile.encoding=UTF-8 \
  -DpropertiesFile=/opt/artixcs-rest/application.properties \
  -jar /opt/artixcs-rest/artixcs-rest.jar
```

Параметры:
- `-Xms256m` — начальный размер heap (JVM резервирует сразу)
- `-Xmx768m` — максимальный размер heap (жёсткий потолок)
- `-Dmaven.repo.local=...` — путь к локальному Maven-репозиторию (thin-launcher ищет зависимости здесь)

---

### 1.5 Установка лимитов всем сервисам

#### Шаг 1 — находим все сервисы без лимитов

```bash
grep -l "ExecStart" /etc/systemd/system/artixcs-*.service | \
  xargs grep -L "Xmx"
```

Вернёт список файлов где нет `-Xmx` — это и есть проблемные.

#### Шаг 2 — патчим всех скриптом

```bash
for svc in \
  artixcs-accounting-bonuses-certificates \
  artixcs-accounting-coupons \
  artixcs-accounting-scheduled-impacts \
  artixcs-clickhouse-rest \
  artixcs-consultant-app \
  artixcs-counter \
  artixcs-datatransfer \
  artixcs-issuance-card \
  artixcs-online-card \
  artixcs-telegram-bot \
  artixcs-report \
  artixcs-sales-loader \
  artixcs-sales-ws; do
    f="/etc/systemd/system/${svc}.service"
    sed -i "s|ExecStart=/opt/${svc}/${svc}.jar|ExecStart=/opt/java-artix/bin/java -Xms128m -Xmx512m -Dsun.misc.URLClassPath.disableJarChecking=true -jar /opt/${svc}/${svc}.jar|g" "$f"
    echo "Patched: $svc"
done
```

#### Шаг 3 — добавляем maven.repo.local тем кто использует thin-launcher

Часть сервисов хранит зависимости локально и без этого параметра падает с ошибкой `Cannot locate launcher`. Определить таких можно по первоначальному `ps aux` — они передавали `-Dmaven.repo.local=` при запуске.

```bash
for svc in \
  artixcs-counter \
  artixcs-accounting-coupons \
  artixcs-accounting-scheduled-impacts \
  artixcs-accounting-bonuses-certificates \
  artixcs-report \
  artixcs-sales-ws \
  artixcs-clickhouse-rest; do
    f="/etc/systemd/system/${svc}.service"
    sed -i "s|-Dsun.misc.URLClassPath.disableJarChecking=true -jar|-Dsun.misc.URLClassPath.disableJarChecking=true -Dmaven.repo.local=/opt/${svc}/lib/repository -jar|g" "$f"
    echo "Patched maven: $svc"
done
```

#### Шаг 4 — применяем и перезапускаем

```bash
systemctl daemon-reload

for svc in \
  artixcs-counter artixcs-accounting-coupons \
  artixcs-accounting-scheduled-impacts artixcs-accounting-bonuses-certificates \
  artixcs-report artixcs-sales-ws artixcs-clickhouse-rest \
  artixcs-datatransfer artixcs-issuance-card artixcs-online-card \
  artixcs-telegram-bot artixcs-consultant-app; do
    systemctl restart "$svc"
    sleep 3
    echo "$svc: $(systemctl is-active $svc)"
done
```

#### Шаг 5 — проверяем что лимиты реально применились

```bash
systemctl status artixcs-datatransfer
```

В секции CGroup должна быть **одна строка** с java и `-Xmx`:
```
CGroup: └─35592 /opt/java-artix/bin/java -Xms128m -Xmx512m ...
```

Если видишь `/bin/bash` — сервис ещё не перезапустился со старым процессом.

---

### 1.6 Нестандартные сервисы — индивидуальные проблемы при запуске

После массового патча большинство сервисов поднялось нормально. Но четыре потребовали отдельного разбирательства. Общий подход для диагностики любого падающего сервиса:

```bash
# Смотрим ошибку напрямую — journalctl её скрывает из-за StandardOutput=null
/opt/java-artix/bin/java [параметры из юнита] 2>&1 | head -30
```

---

#### artixcs-datatransfer — не хватало maven.repo.local

Сервис падал с ошибкой:
```
Cannot locate launcher: /root/.m2/repository/.../spring-boot-thin-launcher...
```

Thin-launcher искал зависимости в `/root/.m2` которого не существует. Решение — добавить путь к локальному репозиторию в юнит:

```ini
ExecStart=/opt/java-artix/bin/java \
  -Xms128m -Xmx512m \
  -Dsun.misc.URLClassPath.disableJarChecking=true \
  -Dmaven.repo.local=/opt/artixcs-datatransfer/lib/repository \
  -jar /opt/artixcs-datatransfer/artixcs-datatransfer.jar
```

Этот паттерн оказался общим — все thin-launcher сервисы нуждались в этом параметре. Список таких сервисов определялся по первоначальному `ps aux`: те у кого в командной строке был `-Dmaven.repo.local` до инцидента.

---

#### artixcs-report — неправильная версия JVM + offline режим

Сервис падал с:
```
Could not transfer artifact su.artix.controlcenter5:root:pom:5.0.0
from/to spring-snapshots: status code: 401
```

Thin-launcher пытался скачать родительский POM из интернета и получал 401. Два шага к решению:

**Шаг 1** — в оригинальном `ps aux` этот сервис запускался через `java-11`, а не стандартный `java-artix`. Всегда смотри первоначальный вывод ps чтобы понять какая JVM нужна сервису.

**Шаг 2** — добавить флаги offline-режима чтобы thin-launcher не лез в интернет:

```ini
ExecStart=/opt/java-11-artix/bin/java \
  -Xms128m -Xmx512m \
  -Dsun.misc.URLClassPath.disableJarChecking=true \
  -Dmaven.repo.local=/opt/artixcs-report/lib/repository \
  -Dthin.offline=true \
  -Dthin.root=/opt/artixcs-report/lib \
  -jar /opt/artixcs-report/artixcs-report.jar
```

Параметры:
- `-Dthin.offline=true` — запрещает thin-launcher обращаться в интернет
- `-Dthin.root=...` — указывает где искать все зависимости локально

> **Важно:** если сервис категорически не хочет стартовать с прямым вызовом JVM — можно временно вернуть bash-обёртку (`ExecStart=/opt/artixcs-report/artixcs-report.jar`). Сервис поднимется без `-Xmx`, но хотя бы будет работать пока не разберёшься с проблемой.

---

#### artixcs-clickhouse-rest — та же проблема, то же решение

Аналогичная ошибка с 401 при попытке скачать POM. Сервис тоже требовал `java-21` (видно из оригинального ps aux) и те же offline-флаги:

```ini
ExecStart=/opt/java-21-artix/bin/java \
  -Xms128m -Xmx512m \
  -Dsun.misc.URLClassPath.disableJarChecking=true \
  -Dmaven.repo.local=/opt/artixcs-clickhouse-rest/lib/repository \
  -Dthin.offline=true \
  -Dthin.root=/opt/artixcs-clickhouse-rest/lib \
  -jar /opt/artixcs-clickhouse-rest/artixcs-clickhouse-rest.jar
```

---

#### artixcs-consultant-app и artixcs-telegram-bot — только неправильная JVM

Оба сервиса запускались нормально — достаточно было указать правильную версию JVM. Никаких maven-параметров не требовалось, так как они не используют thin-launcher.

```ini
# consultant-app
ExecStart=/opt/java-21-artix/bin/java \
  -Xms128m -Xmx512m \
  -Dsun.misc.URLClassPath.disableJarChecking=true \
  -jar /opt/artixcs-consultant-app/artixcs-consultant-app.jar

# telegram-bot
ExecStart=/opt/java-21-artix/bin/java \
  -Xms128m -Xmx512m \
  -Dsun.misc.URLClassPath.disableJarChecking=true \
  -jar /opt/artixcs-telegram-bot/artixcs-telegram-bot.jar
```

---

#### Итоговая таблица — какая JVM и какие параметры нужны каждому сервису

| Сервис | JVM | maven.repo.local | thin.offline |
|---|---|---|---|
| artixcs-rest | java-artix | ✅ | — |
| artixcs-datatransfer | java-artix | ✅ | — |
| artixcs-counter | java-artix | ✅ | — |
| artixcs-accounting-coupons | java-artix | ✅ | — |
| artixcs-accounting-scheduled-impacts | java-artix | ✅ | — |
| artixcs-accounting-bonuses-certificates | java-artix | ✅ | — |
| artixcs-sales-ws | java-artix | ✅ | — |
| artixcs-sales-loader | java-artix | — | — |
| artixcs-issuance-card | java-artix | — | — |
| artixcs-online-card | java-artix | — | — |
| artixcs-controlcenter | java-artix | — | — |
| artixcs-report | **java-11** | ✅ | ✅ |
| artixcs-clickhouse-rest | **java-21** | ✅ | ✅ |
| artixcs-consultant-app | **java-21** | — | — |
| artixcs-telegram-bot | **java-21** | — | — |

> **Главный урок:** всегда сохраняй вывод `ps aux` до того как что-то трогать. В нём видно какая JVM использовалась и какие параметры передавались — это единственный источник правды о том как сервис должен запускаться.

---

### 1.7 Если лимиты не помогли — порядок загрузки сервисов

Даже с `-Xmx` все 14 сервисов при одновременном старте создают огромный пиковый спрос: JVM инициализирует Spring-контекст, загружает классы, прогревает кэши. Пиковое потребление при старте всегда значительно выше рабочего.

#### Способ 1 — задержка между запусками (RestartSec + After)

Добавить зависимости в юниты чтобы сервисы поднимались последовательно. Пример:

```ini
[Unit]
Description=artixcs-accounting-coupons
After=artixcs-rest.service
Requires=artixcs-rest.service
```

Так `artixcs-accounting-coupons` не стартует пока не поднимется `artixcs-rest`.

#### Способ 2 — явная задержка через ExecStartPre

Если точный порядок не важен, но нужно растянуть старт во времени:

```ini
[Service]
ExecStartPre=/bin/sleep 30
ExecStart=/opt/java-artix/bin/java ...
```

Разным сервисам ставим разные задержки: 0, 15, 30, 45 секунд и т.д.

#### Способ 3 — MemoryMax в systemd (вторая линия защиты)

Параметр `-Xmx` ограничивает только heap — ту часть памяти где JVM хранит объекты приложения. Но JVM использует и другую память помимо heap:

- **Metaspace** — память где хранится информация о загруженных классах (код, структуры данных). Может неконтролируемо расти если приложение динамически загружает много классов.
- **Thread stacks** — каждый поток выполнения (thread) резервирует свой стек памяти. Чем больше потоков у приложения — тем больше суммарный расход.
- **Native memory** — память которую JVM берёт напрямую у операционной системы в обход heap, например для буферов ввода-вывода.

Суммарно эти три составляющие могут добавить 100-300 MB сверх лимита `-Xmx`. Поэтому `MemoryMax` в systemd — это вторая линия защиты: он ограничивает **весь процесс целиком** на уровне операционной системы, независимо от того что делает JVM внутри.

```ini
[Service]
ExecStart=/opt/java-artix/bin/java -Xms128m -Xmx512m ...
MemoryMax=700M
MemorySwapMax=0
```

`MemorySwapMax=0` запрещает процессу использовать своп — при превышении лимита OOM Killer убьёт только этот процесс, а не потащит за собой всю систему.

#### Способ 4 — отключить автозапуск тяжёлых сервисов

> ⚠️ **Крайняя мера.** Применять только если сервисы действительно не нужны постоянно, или если сервер физически не может поднять все сервисы одновременно даже с лимитами. В обычной ситуации лучше решать проблему через лимиты памяти и порядок запуска.

Сервисы которые нужны не всегда — убрать из автозапуска и поднимать вручную по необходимости:

```bash
systemctl disable artixcs-sales-loader
systemctl disable artixcs-report
```

Запускать когда нужны:
```bash
systemctl start artixcs-report
```

#### Итоговая рекомендация по стратегии

```
Уровень 1 (обязательно): -Xmx для каждого сервиса
Уровень 2 (желательно):  MemoryMax + MemorySwapMax=0 в systemd
Уровень 3 (при проблемах): After= зависимости или ExecStartPre sleep
Уровень 4 (тюнинг):     Отключить ненужные сервисы из автозапуска
```

---

### 1.8 Мониторинг — чтобы не повторилось

У вас уже стоят Grafana + Prometheus. Нужно настроить алерт:

```yaml
# prometheus alert rule
- alert: LowMemory
  expr: node_memory_MemAvailable_bytes < 1073741824  # < 1 GB
  for: 2m
  labels:
    severity: critical
  annotations:
    summary: "Свободной памяти меньше 1 GB на {{ $labels.instance }}"
```

**Полезные команды для быстрой диагностики:**

```bash
# Общая картина памяти
free -h

# Топ по памяти
ps aux --sort=-%mem | head -20

# Load и uptime
uptime

# Интерактивный мониторинг
top

# Проверить лимиты конкретного сервиса
grep -E "Xmx|MemoryMax" /etc/systemd/system/artixcs-rest.service

# Найти все сервисы без -Xmx
grep -l ExecStart /etc/systemd/system/artixcs-*.service | xargs grep -L Xmx

# Следить за ростом памяти у сервиса в динамике
watch -n 5 'systemctl status artixcs-rest | grep Memory'
```

---

## Часть 2. Хэширование паролей BCrypt — проблема после восстановления

### 2.1 Что произошло

После восстановления MongoDB пароли пользователей в коллекции `webUser` хранились в **открытом виде (plain text)**:

```json
{ "_id": "admin", "password": "admin" }
{ "_id": "user",  "password": "user"  }
```

Приложение использует Spring Security с `BCryptPasswordEncoder` и при логине пытается сравнить введённый пароль с хэшем через BCrypt. Plain text не распознаётся как валидный BCrypt-хэш, и Spring выбрасывает исключение:

```
Encoded password does not look like BCrypt
Wrong user or password
```

### 2.2 Почему $2b$ не подошёл

При первой попытке исправления Python сгенерировал хэши с префиксом `$2b$`:

```
$2b$12$G7MtMTH0XQLjAVcYNjSg1...
```

Spring Security старых версий (1.x / 2.x) распознаёт **только** `$2a$`. Формат `$2b$` появился позже и в старых версиях не поддерживается. Криптографически они идентичны — разница только в букве версии.

### 2.3 Правильная генерация хэшей

```bash
pip install bcrypt --break-system-packages

python3 -c "
import bcrypt
pwd = b'твой_пароль'
h = bcrypt.hashpw(pwd, bcrypt.gensalt(rounds=10)).decode().replace('\$2b\$', '\$2a\$')
print(h)
"
```

Хэш должен начинаться с `$2a$10$...`

### 2.4 Обновление паролей в MongoDB

```javascript
use artixcs

db.webUser.updateOne(
  { _id: "admin" },
  { $set: { password: "$2a$10$..." } }
)

db.webUser.updateOne(
  { _id: "user" },
  { $set: { password: "$2a$10$..." } }
)
```

### 2.5 Проверка — нет ли других незахэшированных паролей

```javascript
// Найти всех пользователей у кого пароль не BCrypt
db.webUser.find(
  { password: { $not: /^\$2[ab]\$/ } },
  { _id: 1, password: 1 }
).pretty()
```

Если вернёт документы — у них plain text пароли, нужно захэшировать по той же схеме.

### 2.6 Итог по паролям

| Проблема | Причина | Решение |
|---|---|---|
| `Encoded password does not look like BCrypt` | Пароль в plain text в MongoDB | Захэшировать через BCrypt |
| Хэш `$2b$` не принимается | Старая версия Spring Security | Заменить `$2b$` на `$2a$` в хэше |

---

## Итоги по всему инциденту

| # | Проблема | Причина | Что сделали |
|---|---|---|---|
| 1 | OOM-коллапс при старте | 14 сервисов без -Xmx запустились одновременно | Добавили -Xmx всем сервисам, перешли на прямой вызов JVM |
| 2 | Сервисы падали после патча | Thin-launcher не мог найти зависимости без -Dmaven.repo.local | Добавили параметр в юниты тем кто нуждался |
| 3 | artixcs-report и clickhouse-rest падали с 401 | Thin-launcher лез в интернет за POM-файлами | Добавили -Dthin.offline=true и -Dthin.root= |
| 4 | consultant-app и telegram-bot не стартовали | Неправильная версия JVM в юните (java-artix вместо java-21) | Указали правильную JVM согласно оригинальному ps aux |
| 5 | Невозможно войти в систему | Пароли в MongoDB хранились в plain text | Перехэшировали в BCrypt с префиксом $2a$ |
