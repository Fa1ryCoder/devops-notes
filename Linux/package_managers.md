# Пакетные менеджеры в Linux
> Углублённая лекция · DevOps Middle+

---

## Содержание

1. [Что такое пакет и пакетный менеджер](#1-что-такое-пакет-и-пакетный-менеджер)
2. [Архитектура: dpkg и apt — два уровня](#2-архитектура-dpkg-и-apt)
3. [apt — работа с пакетами](#3-apt)
4. [dpkg — низкий уровень](#4-dpkg)
5. [Репозитории — откуда берутся пакеты](#5-репозитории)
6. [Зависимости — как это работает](#6-зависимости)
7. [Удержание пакетов — hold и pinning](#7-hold-и-pinning)
8. [Анти-паттерны](#8-анти-паттерны)
9. [Реальные кейсы с дебагом](#9-реальные-кейсы-с-дебагом)
10. [Вопросы на собесе](#10-вопросы-на-собесе)
11. [Шпаргалка](#11-шпаргалка)

---

## 1. Что такое пакет и пакетный менеджер

### Что такое пакет

**Пакет** — это архив который содержит:
- Скомпилированные бинарные файлы программы
- Конфигурационные файлы
- Документацию
- Метаданные: имя, версия, список зависимостей, описание
- Скрипты которые запускаются до/после установки (`preinst`, `postinst`, `prerm`, `postrm`)

#### Скрипты сопровождающего (maintainer scripts)

Это обычные shell-скрипты упакованные внутрь `.deb`. dpkg автоматически запускает их в нужный момент установки или удаления:

| Скрипт | Когда запускается | Типичные задачи |
|--------|------------------|-----------------|
| `preinst` | **ДО** распаковки файлов | Остановить сервис, создать системного пользователя, проверить условия |
| `postinst` | **ПОСЛЕ** копирования файлов на диск | Запустить сервис (`systemctl enable/start`), сгенерировать дефолтный конфиг, обновить кэш библиотек (`ldconfig`) |
| `prerm` | **ДО** удаления файлов | Остановить сервис, отключить из автозапуска |
| `postrm` | **ПОСЛЕ** удаления файлов | Очистить временные файлы; при `purge` — удалить конфиги и данные |

Скрипт `postrm` получает аргумент (`remove`, `purge`, `upgrade`) и может вести себя по-разному в зависимости от причины удаления.

#### Как скрипты вызываются на практике

Ты их **никогда не вызываешь руками** — это делает `dpkg` автоматически. Но можно наблюдать.

**Что внутри скрипта**

```bash
cat /var/lib/dpkg/info/nginx.postinst
```
```bash
#!/bin/sh
set -e

case "$1" in
    configure)
        # запускается при установке и обновлении
        if [ -x /usr/bin/deb-systemd-helper ]; then
            deb-systemd-helper enable nginx.service
        fi
    ;;
    abort-upgrade|abort-remove|abort-deconfigure)
    ;;
esac
```

Ключевое — `case "$1"`. dpkg передаёт скрипту аргумент и тот знает зачем его вызвали.

**Установи пакет и смотри вывод**

```bash
apt install nginx
```
```
Preparing to unpack .../nginx_1.24.0-1_amd64.deb ...   ← dpkg вызвал preinst
Unpacking nginx (1.24.0-1) ...
Setting up nginx (1.24.0-1) ...                         ← dpkg вызвал postinst
```

`Preparing to unpack` = вызов `preinst install`
`Setting up` = вызов `postinst configure`

**Включи подробный режим и увидишь вызовы явно**

```bash
dpkg --debug=2 -i nginx.deb 2>&1 | grep -E "script|running"
# D000010: running maintainer script 'preinst' with arg 'install'
# D000010: running maintainer script 'postinst' with arg 'configure 1.22.0-1'
```

**Какие аргументы dpkg передаёт скриптам под капотом**

Ты вводишь только `apt install nginx` — аргументы скриптам добавляет **сам dpkg** автоматически, ты их не видишь и не контролируешь:

```
ты вводишь:   apt install nginx
apt вызывает: dpkg -i nginx.deb
dpkg вызывает внутри:
    ./preinst  install               ← dpkg сам добавил аргумент
    (распаковывает файлы)
    ./postinst configure             ← и здесь тоже
```

Это внутренний протокол dpkg — стандарт Debian описывает какой аргумент передавать в какой момент. Авторы пакета пишут `case "$1"` чтобы скрипт мог различить ситуации:

```
apt install nginx   →   preinst   install
                    →   postinst  configure <предыдущая_версия>

apt remove nginx    →   prerm     remove
                    →   postrm    remove

apt purge nginx     →   prerm     remove
                    →   postrm    purge        ← другой аргумент!

apt upgrade nginx   →   preinst   upgrade <старая_версия>
                    →   postinst  configure <старая_версия>
```

Именно поэтому `postrm` при `purge` удаляет конфиги — он видит аргумент `purge` и делает `rm -rf /etc/nginx`.

**Самый наглядный способ понять — эмулировать вызов dpkg вручную**

```bash
# посмотреть как скрипт ведёт себя с разными аргументами
echo '#!/bin/sh
echo "postinst вызван с аргументом: $1"
' > /tmp/test_postinst

chmod +x /tmp/test_postinst

/tmp/test_postinst configure
# → postinst вызван с аргументом: configure

/tmp/test_postinst purge
# → postinst вызван с аргументом: purge
```

Скрипт — это обычный shell. dpkg просто вызывает его с нужным аргументом в нужный момент.

После установки скрипты лежат в `/var/lib/dpkg/info/`:

```bash
ls /var/lib/dpkg/info/nginx.*
# /var/lib/dpkg/info/nginx.conffiles
# /var/lib/dpkg/info/nginx.list
# /var/lib/dpkg/info/nginx.postinst   ← вот они
# /var/lib/dpkg/info/nginx.prerm

# Посмотреть содержимое:
cat /var/lib/dpkg/info/nginx.postinst
```

На Debian/Ubuntu пакеты имеют расширение `.deb`.

### Зачем нужен пакетный менеджер

Без него нужно было бы:
- Вручную скачивать архив программы
- Вручную отслеживать все зависимости (программа A нужна библиотеку B версии >=2.0)
- Вручную следить за обновлениями безопасности
- Вручную убирать файлы при удалении

Пакетный менеджер делает всё это автоматически: скачивает, разрешает зависимости, устанавливает, обновляет, удаляет.

---

## 2. Архитектура: dpkg и apt

Это важно понять до команд — иначе непонятно почему существуют и `apt`, и `dpkg`, и зачем они оба.

```
┌─────────────────────────────────────────────────┐
│  apt                                            │
│  Высокий уровень:                               │
│  - скачивает пакеты из репозиториев             │
│  - автоматически разрешает зависимости          │
│  - управляет кэшем                              │
│  - обновляет списки пакетов                     │
├─────────────────────────────────────────────────┤
│  dpkg                                           │
│  Низкий уровень:                                │
│  - устанавливает/удаляет конкретный .deb файл  │
│  - ведёт базу установленных пакетов             │
│  - не знает про репозитории и зависимости       │
└─────────────────────────────────────────────────┘
```

Когда вы запускаете `apt install nginx` — apt скачивает нужные `.deb` файлы и зависимости, а для реальной установки каждого файла вызывает `dpkg`. dpkg при этом не знает что его вызвал apt — он просто устанавливает пакет.

Если скачать `.deb` файл вручную и установить через `dpkg -i` — зависимости не подтянутся автоматически. Это задача apt, не dpkg.

```bash
# база данных установленных пакетов (dpkg ведёт её сам)
/var/lib/dpkg/status        # статус всех пакетов
/var/lib/dpkg/info/         # файлы каждого пакета, скрипты установки
/var/cache/apt/archives/    # кэш скачанных .deb файлов
```

---

## 3. apt

### Основные команды

```bash
# ── ОБНОВЛЕНИЕ ИНФОРМАЦИИ О ПАКЕТАХ ──────────────────────────────
apt update
# Скачивает актуальные списки пакетов из репозиториев.
# НЕ обновляет сами пакеты — только метаданные.
# Запускать перед любой установкой, особенно в скриптах.

# ── УСТАНОВКА ────────────────────────────────────────────────────
apt install nginx
apt install nginx postgresql redis    # несколько сразу
apt install nginx=1.24.0-1            # конкретная версия
apt install ./package.deb             # локальный .deb с разрешением зависимостей
apt install -y nginx                  # без интерактивного подтверждения (для скриптов)

# ── УДАЛЕНИЕ ─────────────────────────────────────────────────────
apt remove nginx
# Удаляет пакет, оставляет конфиги в /etc/

apt purge nginx
# Удаляет пакет И конфиги
# Используйте purge при полной деинсталляции

apt autoremove
# Удаляет пакеты которые были установлены как зависимости
# но больше не нужны ни одному пакету

apt autoremove --purge
# То же + удаляет конфиги зависимостей

# ── ОБНОВЛЕНИЕ ПАКЕТОВ ────────────────────────────────────────────
apt upgrade
# Обновляет все пакеты. Не удаляет пакеты и не меняет зависимости.
# Безопасный вариант.

apt full-upgrade
# Обновляет всё, может удалять конфликтующие пакеты и добавлять новые.
# Нужен для обновления дистрибутива (dist-upgrade).

apt upgrade nginx
# Обновить конкретный пакет

# ── ПОИСК И ИНФОРМАЦИЯ ────────────────────────────────────────────
apt search nginx
# Поиск по имени и описанию в доступных пакетах

apt show nginx
# Подробная информация: версия, зависимости, описание, размер

apt list --installed
# Список всех установленных пакетов

apt list --installed | grep nginx
# Установлен ли конкретный пакет

apt list --upgradable
# Список пакетов для которых есть обновление

# ── КЭШ ──────────────────────────────────────────────────────────
apt clean
# Удалить все скачанные .deb из кэша (/var/cache/apt/archives/)
# Используйте для освобождения места

apt autoclean
# Удалить только устаревшие .deb (версии которых больше нет в репозиториях)
```

### apt vs apt-get — в чём разница

```
apt-get    — старый инструмент, стабильный API, используется в скриптах
apt        — новый, более удобный вывод, прогресс-бар, рекомендован для людей

Правило: в скриптах и Dockerfile → apt-get (стабильный вывод, нет предупреждений)
         в терминале вручную → apt (удобнее)
```

```bash
# в Dockerfile всегда apt-get
RUN apt-get update && apt-get install -y --no-install-recommends \
    nginx \
    curl \
    && rm -rf /var/lib/apt/lists/*
```

Флаг `--no-install-recommends` — не устанавливать рекомендованные пакеты (только обязательные зависимости). Уменьшает размер образа.

### История установок

```bash
# /var/log/apt/history.log — что устанавливалось и когда
cat /var/log/apt/history.log | grep -A4 "Start-Date: 2024-01"

# вывод:
# Start-Date: 2024-01-15  10:32:01
# Commandline: apt install nginx
# Install: nginx:amd64 (1.24.0-1), nginx-core:amd64 (1.24.0-1)
# End-Date: 2024-01-15  10:32:08

# полный лог включая dpkg операции
/var/log/dpkg.log
```

---

## 4. dpkg

### Когда нужен dpkg напрямую

dpkg нужен когда:
- Устанавливаете `.deb` файл скачанный вручную (например от вендора)
- Диагностируете проблемы с конкретным пакетом
- Смотрите что именно установил пакет (список файлов)
- Работаете со сломанной базой пакетов

```bash
# ── УСТАНОВКА ────────────────────────────────────────────────────
dpkg -i package.deb
# Установить .deb файл
# Зависимости НЕ подтягиваются — если нет зависимостей: ошибка
# После ошибки зависимостей: apt install -f (fix broken)

# ── УДАЛЕНИЕ ─────────────────────────────────────────────────────
dpkg -r nginx           # удалить (конфиги остаются)
dpkg -P nginx           # purge (удалить + конфиги)

# ── ИНФОРМАЦИЯ ────────────────────────────────────────────────────
dpkg -l                 # список всех пакетов
dpkg -l nginx           # статус конкретного пакета
dpkg -l | grep nginx    # поиск по имени

# расшифровка статусных символов в dpkg -l:
# ii  — установлен нормально (installed)
# rc  — удалён, конфиги остались (removed, config-files)
# un  — неизвестен (unknown, not installed)
# hi  — удержан (hold)
# pn  — полностью удалён (purged, not installed)

dpkg -L nginx           # список файлов установленных пакетом
# /etc/nginx
# /etc/nginx/nginx.conf
# /usr/sbin/nginx
# ...

dpkg -S /usr/sbin/nginx # какой пакет установил этот файл
# nginx: /usr/sbin/nginx

dpkg --status nginx     # подробная информация о пакете
dpkg --print-architecture  # архитектура системы (amd64, arm64...)

# ── КОНФИГУРАЦИОННЫЕ ФАЙЛЫ ────────────────────────────────────────
# dpkg отслеживает конфигурационные файлы пакетов
# При обновлении если вы изменили конфиг — dpkg спросит что делать:
# Y — оставить ваш вариант
# N — взять новый из пакета
# D — показать diff

# Принудительно оставить текущий конфиг при обновлении (без вопросов):
apt-get -o Dpkg::Options::="--force-confold" upgrade

# Принудительно взять новый конфиг из пакета:
apt-get -o Dpkg::Options::="--force-confnew" upgrade
```

### Восстановление сломанной базы пакетов

```bash
# пакет установлен наполовину — привести в порядок
dpkg --configure -a
apt install -f          # -f = --fix-broken

# принудительно удалить проблемный пакет
dpkg --remove --force-remove-reinstreq broken-package
```

---

## 5. Репозитории

### Что такое репозиторий

**Репозиторий** — это сервер (или его зеркало) на котором хранятся пакеты с метаданными: список пакетов, их версии, хэши для проверки целостности, GPG подписи.

### Хэши и GPG подписи — как это работает

Это два разных механизма защиты которые работают вместе:

**SHA256 хэш — проверка целостности**

Контрольная сумма файла. Репозиторий вычисляет SHA256 от каждого файла и публикует результат. При скачивании `apt` вычисляет хэш заново и сравнивает. Не совпадает — файл повреждён при передаче или подменён. Хэш отвечает на вопрос: *«файл дошёл без изменений?»*

**GPG подпись — проверка подлинности**

Хэшей недостаточно: злоумышленник может подменить и файл, и хэш к нему. Поэтому репозиторий подписывает файл `InRelease` (который внутри содержит SHA256 хэши всех пакетных списков) своим **приватным GPG ключом**. На вашей машине хранится **публичный ключ** — `apt` проверяет подпись им. Подделать подпись без приватного ключа невозможно. GPG отвечает на вопрос: *«это действительно прислал тот, кому я доверяю?»*

```
Цепочка доверия:

  GPG подпись
      │
      └── защищает ──► InRelease (содержит SHA256 хэши Packages.gz)
                                         │
                                         └── хэши ──► каждый .deb файл
```

Итог: **один GPG ключ защищает всё остальное сверху вниз**.

### Что происходит при apt update и apt install

Именно здесь обе проверки и применяются:

```
apt update                               apt install nginx
     │                                         │
     ▼                                         ▼
Читает sources.list                Ищет пакет в локальном кэше
     │                             (берёт URL и ожидаемый SHA256)
     ▼                                         │
Скачивает InRelease с сервера                  ▼
     │                                  Скачивает .deb файл
     ▼                                         │
[GPG проверка подписи]           ─────► [SHA256 проверка]
 Издатель подлинный?                    Файл не подменён?
     │                                         │
     ▼                                         ▼
Скачивает Packages.gz              dpkg: запускает preinst
     │                             (стоп сервисов, пре-проверки)
     ▼                                         │
[SHA256 проверка]                             ▼
 Данные не повреждены?             Распаковывает файлы на диск
     │                                         │
     ▼                                         ▼
Кэш пакетов обновлён               dpkg: запускает postinst
                                   (старт сервисов, настройка)
```

`apt update` — единственное место где проверяется **GPG**. После этого локальный кэш считается доверенным.

`apt install` — GPG повторно не проверяется. Но SHA256 проверяется снова — уже для скачанного `.deb` файла (хэш сверяется с тем что записан в Packages.gz).

### /etc/apt/sources.list и sources.list.d/

```bash
cat /etc/apt/sources.list
# deb http://deb.debian.org/debian bookworm main contrib non-free non-free-firmware
# deb http://security.debian.org/debian-security bookworm-security main
# deb http://deb.debian.org/debian bookworm-updates main

# Разбор строки:
# deb  — бинарные пакеты (deb-src — исходники)
# http://deb.debian.org/debian  — URL репозитория
# bookworm  — название релиза (codename дистрибутива)
# main contrib non-free  — секции (компоненты)
```

**Секции пакетов:**

| Секция | Что содержит |
|--------|-------------|
| `main` | Свободное ПО полностью поддерживаемое Debian |
| `contrib` | Свободное ПО зависящее от несвободных компонентов |
| `non-free` | Несвободное ПО |
| `non-free-firmware` | Несвободные прошивки (драйверы) |

### Современный формат DEB822

Новый формат репозиториев (Debian 12+, Ubuntu 22.04+) — файлы `.sources` в `/etc/apt/sources.list.d/`:

```bash
cat /etc/apt/sources.list.d/debian.sources
# Types: deb
# URIs: http://deb.debian.org/debian
# Suites: bookworm bookworm-updates
# Components: main contrib non-free non-free-firmware
# Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
```

### Добавление стороннего репозитория (правильный способ)

Раньше ключи репозиториев добавляли через `apt-key add` — этот метод устарел и небезопасен (ключ доверялся всем репозиториям). Сейчас правильно:

```bash
# Пример: добавить репозиторий Docker
# Шаг 1: скачать и сохранить GPG ключ
curl -fsSL https://download.docker.com/linux/debian/gpg \
    | gpg --dearmor \
    -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

# Шаг 2: добавить репозиторий с привязкой к конкретному ключу
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/debian \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | tee /etc/apt/sources.list.d/docker.list

# Шаг 3: обновить и установить
apt update
apt install docker-ce

# Директория для ключей
ls /etc/apt/keyrings/
# docker.gpg  nginx.gpg  postgresql.gpg  ...
```

### apt-cache — работа с кэшем метаданных

```bash
# поиск пакетов (быстрее чем apt search, работает офлайн)
apt-cache search nginx
apt-cache search "web server"

# подробная информация о пакете
apt-cache show nginx

# зависимости пакета
apt-cache depends nginx
# nginx
#   Depends: nginx-core | nginx-full | nginx-light | nginx-extras
#   Depends: libnginx-mod-http-gzip-static

# кто зависит от этого пакета (обратные зависимости)
apt-cache rdepends nginx

# политика версий (откуда возьмётся пакет, приоритеты)
apt-cache policy nginx
# nginx:
#   Installed: 1.24.0-1
#   Candidate: 1.24.0-1
#   Version table:
#  *** 1.24.0-1 500
#         500 http://deb.debian.org/debian bookworm/main amd64
#         100 /var/lib/dpkg/status
```

---

## 6. Зависимости

### Как работают зависимости

Каждый пакет в метаданных указывает от чего зависит. apt строит граф зависимостей и устанавливает всё нужное в правильном порядке.

Типы зависимостей в `.deb`:

| Тип | Смысл |
|-----|-------|
| `Depends` | Обязательная зависимость — без неё пакет не работает |
| `Recommends` | Рекомендуется — устанавливается по умолчанию, но не обязательна |
| `Suggests` | Предлагается — не устанавливается автоматически |
| `Pre-Depends` | Должна быть установлена ДО распаковки пакета |
| `Conflicts` | Несовместим с этим пакетом |
| `Replaces` | Заменяет другой пакет |
| `Provides` | Виртуальный пакет — предоставляет функциональность |

```bash
# посмотреть зависимости пакета
apt-cache depends nginx

# посмотреть что реально будет установлено
apt install --dry-run nginx
apt install -s nginx         # -s = simulate, то же самое

# вывод:
# Inst libnginx-mod-http-gzip-static (1.24.0-1 Debian:bookworm)
# Inst nginx-core (1.24.0-1 Debian:bookworm)
# Inst nginx (1.24.0-1 Debian:bookworm)
# Conf libnginx-mod-http-gzip-static ...
```

### Виртуальные пакеты

Виртуальный пакет — это имя которое предоставляет функциональность, но само не является реальным пакетом. Реальные пакеты могут объявлять что они `Provides: virtual-package`.

```bash
# mail-transport-agent — виртуальный пакет
# его могут предоставлять: postfix, exim4, sendmail, nullmailer
apt install mail-transport-agent
# apt выберет один из доступных провайдеров

# посмотреть кто предоставляет виртуальный пакет
apt-cache showpkg mail-transport-agent | grep "Reverse Provides"
```

---

## 7. Hold и pinning

### Hold — удержать версию пакета

**Hold** — запрет автоматического обновления конкретного пакета. Нужно когда:
- Новая версия сломала что-то в вашем стеке
- Нужна конкретная версия для совместимости
- Хотите обновлять вручную в контролируемый момент

```bash
# поставить hold
apt-mark hold nginx
# nginx set on hold.

# снять hold
apt-mark unhold nginx

# посмотреть удержанные пакеты
apt-mark showhold
# nginx

# через dpkg
echo "nginx hold" | dpkg --set-selections
dpkg --get-selections | grep hold

# проверить статус
dpkg -l nginx
# hi  nginx  1.24.0-1  amd64  high performance web server
# ↑
# h = hold
```

При `apt upgrade` пакет с hold будет пропущен:
```
The following packages have been kept back:
  nginx
```

### apt-cache policy — приоритеты и pinning

**Pinning** — механизм управления приоритетами репозиториев и версий. Позволяет:
- Взять конкретную версию из определённого репозитория
- Понизить/повысить приоритет всего репозитория
- Установить пакет из testing не обновляя всё остальное

```bash
# посмотреть текущие приоритеты
apt-cache policy
# Package files:
#  100 /var/lib/dpkg/status
#      release a=now
#  500 http://deb.debian.org/debian bookworm/main
#      release v=12.0,o=Debian,a=stable,n=bookworm

# приоритеты по умолчанию:
# 100 — уже установленные пакеты
# 500 — из репозитория (дефолт)
# 990 — установлено вручную через apt install pkg=version
# 1001 — принудительная установка (downgrade)
# <0  — никогда не устанавливать
```

Создать pin файл `/etc/apt/preferences.d/`:

```bash
# Удержать nginx на конкретной версии
cat > /etc/apt/preferences.d/nginx << 'EOF'
Package: nginx
Pin: version 1.24.0-1
Pin-Priority: 1001
EOF

# Никогда не устанавливать пакет
cat > /etc/apt/preferences.d/no-postfix << 'EOF'
Package: postfix
Pin: release *
Pin-Priority: -1
EOF

# Взять пакет из backports, остальное из stable
cat > /etc/apt/preferences.d/backports << 'EOF'
Package: *
Pin: release a=bookworm-backports
Pin-Priority: 100

Package: golang
Pin: release a=bookworm-backports
Pin-Priority: 900
EOF
```

```bash
# проверить результат
apt-cache policy nginx
# nginx:
#   Installed: 1.24.0-1
#   Candidate: 1.24.0-1   ← pinning сработал
#   Pin: (not found)

## 8. Анти-паттерны

### apt update без upgrade в скриптах

```bash
# ❌ — обновляет только метаданные, пакеты не обновляет
apt update

# Типичная ошибка в скриптах деплоя:
# "мы всегда делаем apt update перед установкой" — но не делают upgrade
# Уязвимости в установленных пакетах остаются

# ✅ — обновить и пакеты
apt update && apt upgrade -y
```

---

### apt-get без флага -y в скриптах

```bash
# ❌ — скрипт зависнет ожидая интерактивного ввода
apt-get install nginx

# ✅ — для скриптов и CI/CD
apt-get install -y nginx
# или с переменной окружения (убирает все интерактивные вопросы)
DEBIAN_FRONTEND=noninteractive apt-get install -y nginx
```

---

### Удаление пакетов без autoremove

```bash
# ❌ — зависимости остаются, занимают место
apt remove nginx

# ✅ — убираем и зависимости которые больше не нужны
apt remove nginx && apt autoremove
# или сразу
apt autoremove nginx
```

---

### Использование apt-key add (устаревший способ)

```bash
# ❌ — небезопасно: ключ доверяется ВСЕМ репозиториям
curl -fsSL https://example.com/key.gpg | apt-key add -

# ✅ — ключ привязан к конкретному репозиторию
curl -fsSL https://example.com/key.gpg \
    | gpg --dearmor \
    -o /etc/apt/keyrings/example.gpg
# и в sources.list.d указать: signed-by=/etc/apt/keyrings/example.gpg
```

---

### Установка пакетов в Dockerfile без чистки кэша

```dockerfile
# ❌ — кэш apt остаётся в слое, образ раздувается
RUN apt-get update
RUN apt-get install -y nginx

# ❌ — отдельный RUN не помогает, кэш уже в предыдущем слое
RUN apt-get update
RUN apt-get install -y nginx
RUN rm -rf /var/lib/apt/lists/*

# ✅ — один RUN, кэш чистится в том же слое
RUN apt-get update && apt-get install -y --no-install-recommends \
    nginx \
    curl \
    && rm -rf /var/lib/apt/lists/*
```

---

### Игнорирование GPG верификации

```bash
# ❌ — никогда в продакшне
apt-get install -y --allow-unauthenticated nginx
wget http://example.com/package.deb && dpkg -i package.deb  # без проверки подписи

# ✅ — всегда проверять подпись
# GPG верификация включена по умолчанию в apt
# Добавляйте ключи правильно через /etc/apt/keyrings/
```

---

## 9. Реальные кейсы с дебагом

### Кейс 1: «apt install зависает на Configure»

**Симптом:** `apt install` завис на `Setting up package...` и не двигается.

```bash
# Причина: пакет ждёт ответа на вопрос конфигурации в фоне

# Диагностика: смотрим что происходит
ps aux | grep dpkg
# dpkg -i --force-confold nginx.deb

# Решение 1: установить переменную DEBIAN_FRONTEND
DEBIAN_FRONTEND=noninteractive apt install -y nginx

# Решение 2: ответить на вопросы заранее через debconf
echo "nginx nginx/start-daemon boolean true" | debconf-set-selections
apt install -y nginx

# Решение 3: если уже завис — убить и починить
kill $(pgrep dpkg)
dpkg --configure -a
apt install -f
```

---

### Кейс 2: «Пакет установлен наполовину — система не работает»

**Симптом:** Прерванная установка (Ctrl+C, reboot), теперь `apt` ругается на broken packages.

```bash
# Симптом:
apt install curl
# dpkg: error processing package nginx (--configure):
#  subprocess installed post-installation script returned error exit status 1
# Errors were encountered while processing: nginx

# Шаг 1: завершить прерванные настройки
dpkg --configure -a

# Шаг 2: починить сломанные зависимости
apt install -f

# Шаг 3: если dpkg --configure -a тоже падает
# принудительно удалить проблемный пакет
dpkg --remove --force-remove-reinstreq nginx
apt install nginx   # установить заново

# Шаг 4: проверить что всё хорошо
dpkg --audit        # показать проблемные пакеты
```

---

### Кейс 3: «Нужна старая версия пакета»

**Симптом:** Обновили nginx, новая версия несовместима с конфигом. Нужно откатиться.

```bash
# Шаг 1: найти доступные версии
apt-cache policy nginx
# nginx:
#   Installed: 1.26.0-1
#   Candidate: 1.26.0-1
#   Version table:
#  *** 1.26.0-1 500
#         500 http://deb.debian.org/debian bookworm/main amd64
#      1.24.0-1 500
#         500 http://deb.debian.org/debian bookworm/main amd64

# Шаг 2: откатиться на нужную версию
apt install nginx=1.24.0-1

# Шаг 3: зафиксировать чтобы не обновлялось само
apt-mark hold nginx

# Шаг 4: проверить
apt-mark showhold
dpkg -l nginx
# hi  nginx  1.24.0-1  ...  ← h = hold
```

---

### Кейс 4: «Диск заполнен — нет места для apt»

**Симптом:** `apt install` выдаёт `No space left on device`.

```bash
# Шаг 1: смотрим что занимает место
df -h
du -sh /var/cache/apt/archives/   # кэш apt

# Шаг 2: чистим кэш apt
apt clean                          # удалить все скачанные .deb
apt autoclean                      # удалить только устаревшие

# Шаг 3: удаляем ненужные пакеты
apt autoremove --purge

# Шаг 4: ищем большие пакеты
dpkg-query -Wf '${Installed-Size}\t${Package}\n' | sort -n | tail -20
# размер в KB, сортировка от меньшего к большему

# Шаг 5: удалённые пакеты с оставшимися конфигами
dpkg -l | grep "^rc"
# rc = removed but config files remain
# очистить:
dpkg -l | grep "^rc" | awk '{print $2}' | xargs dpkg --purge
```

---

### Кейс 5: «GPG ошибка при apt update»

**Симптом:**
```
W: GPG error: http://example.com stable InRelease: The following signatures
couldn't be verified because the public key is not available: NO_PUBKEY ABC123
```

```bash
# Причина: ключ репозитория устарел или не добавлен

# Шаг 1: скачать и добавить ключ правильным способом
curl -fsSL https://example.com/signing-key.gpg \
    | gpg --dearmor \
    -o /etc/apt/keyrings/example.gpg

# Шаг 2: обновить файл репозитория
# Добавить в .list или .sources: signed-by=/etc/apt/keyrings/example.gpg

# Шаг 3: если нужно срочно (временный workaround, не для продакшна)
apt-get update --allow-insecure-repositories

# Шаг 4: проверить что ключ добавлен
gpg --no-default-keyring \
    --keyring /etc/apt/keyrings/example.gpg \
    --list-keys
```

---

### Кейс 6: «Какой пакет установил этот файл?»

```bash
# Файл появился на сервере — нужно понять откуда

# Debian/Ubuntu
dpkg -S /usr/bin/curl
# curl: /usr/bin/curl

dpkg -S /etc/nginx/nginx.conf
# nginx-common: /etc/nginx/nginx.conf


rpm -qf /usr/bin/curl
# curl-7.76.1-26.el9.x86_64

# Если файл не принадлежит ни одному пакету:
dpkg -S /usr/local/bin/myapp
# dpkg-query: no path found matching pattern /usr/local/bin/myapp
# → файл установлен вручную, не через пакетный менеджер
```

---

## 10. Вопросы на собесе

### Базовый уровень

**В: В чём разница между `apt update` и `apt upgrade`?**
> О: `apt update` — только обновляет локальные метаданные о доступных пакетах (скачивает списки из репозиториев). Ничего не устанавливает и не обновляет. `apt upgrade` — обновляет сами пакеты до последних доступных версий. Перед upgrade всегда нужен update чтобы знать актуальные версии.

**В: Чем `apt remove` отличается от `apt purge`?**
> О: `remove` удаляет бинарные файлы пакета, но оставляет конфигурационные файлы в `/etc/`. Это позволяет переустановить пакет с теми же настройками. `purge` удаляет пакет вместе с конфигами — чистая деинсталляция. При переносе сервера или чистке старых пакетов лучше использовать purge.

**В: Зачем нужен `dpkg` если есть `apt`?**
> О: apt работает с репозиториями и разрешает зависимости, но для реальной установки вызывает dpkg. dpkg нужен напрямую когда: устанавливаете `.deb` файл скачанный вручную, диагностируете проблемы с базой пакетов, смотрите список файлов установленного пакета (`dpkg -L`), определяете какой пакет установил файл (`dpkg -S`).

---

### Middle уровень

**В: Как запретить обновление конкретного пакета?**
> О: Два способа. `apt-mark hold nginx` — пакет не будет обновляться при `apt upgrade`, помечается как `hi` в `dpkg -l`. Через pinning в `/etc/apt/preferences.d/` — более гибко, можно зафиксировать версию или понизить приоритет репозитория. Проверить: `apt-mark showhold` и `apt-cache policy nginx`.

**В: Что такое GPG верификация в apt и зачем она нужна?**
> О: Каждый репозиторий подписывает свои метаданные GPG ключом. apt проверяет подпись при `apt update` — это гарантирует что пакеты пришли от легитимного источника и не были подменены по пути. Без верификации злоумышленник мог бы подсунуть вредоносный пакет через MITM. Ключи хранятся в `/etc/apt/keyrings/`. Устаревший `apt-key add` небезопасен — ключ доверялся всем репозиториям глобально.

**В: Как найти какой пакет установил конкретный файл?**
> О: `dpkg -S /path/to/file` на Debian, `rpm -qf /path/to/file` на RHEL. Если файл не принадлежит ни одному пакету — установлен вручную. Это полезно при аудите безопасности и диагностике.

**В: В чём преимущество `dnf history undo` перед apt?**
> О: dnf хранит полную историю операций с возможностью отката. `dnf history undo N` возвращает систему к состоянию до операции N — это настоящий downgrade с восстановлением зависимостей. В apt такой функции нет: можно вручную указать версию (`apt install pkg=version`) но автоматического отката операции не существует.

**В: Почему в Dockerfile нужно объединять apt-get update, install и rm в один RUN?**
> О: Каждый `RUN` создаёт отдельный слой образа. Если `apt-get update` в одном слое, а `rm -rf /var/lib/apt/lists/*` в другом — кэш apt физически остаётся в первом слое и попадает в финальный образ. Объединение в один `RUN` гарантирует что кэш не попадёт ни в один слой образа. Флаг `--no-install-recommends` дополнительно уменьшает размер не устанавливая рекомендованные пакеты.

---

## 11. Шпаргалка

### apt — ежедневные команды

```bash
apt update                          # обновить списки пакетов
apt upgrade                         # обновить все пакеты
apt install -y nginx                # установить
apt install nginx=1.24.0-1          # конкретная версия
apt remove nginx                    # удалить (конфиги остаются)
apt purge nginx                     # удалить + конфиги
apt autoremove --purge              # удалить ненужные зависимости
apt search nginx                    # поиск
apt show nginx                      # информация о пакете
apt list --installed                # установленные пакеты
apt list --upgradable               # доступные обновления
apt install --dry-run nginx         # симуляция без установки
apt clean                           # очистить кэш
```

### dpkg — диагностика

```bash
dpkg -l                             # все пакеты
dpkg -l nginx                       # статус пакета
dpkg -L nginx                       # файлы пакета
dpkg -S /usr/sbin/nginx             # чей файл
dpkg --status nginx                 # подробная информация
dpkg --configure -a                 # завершить прерванные установки
dpkg --audit                        # показать проблемные пакеты
dpkg -l | grep "^rc"               # удалённые с оставшимися конфигами
```

### Hold и pinning

```bash
apt-mark hold nginx                 # заморозить версию
apt-mark unhold nginx               # разморозить
apt-mark showhold                   # список заморожённых
apt-cache policy nginx              # версии и приоритеты
```

### dnf (RHEL)

```bash
dnf install nginx                   # установить
dnf remove nginx                    # удалить
dnf update                          # обновить всё
dnf check-update                    # что можно обновить
dnf search nginx                    # поиск
dnf info nginx                      # информация
dnf list installed                  # установленные
dnf history                         # история операций
dnf history undo N                  # откатить операцию N
dnf repolist                        # репозитории
```

### rpm (RHEL, низкий уровень)

```bash
rpm -qa | grep nginx                # установлен ли пакет
rpm -qi nginx                       # информация
rpm -ql nginx                       # файлы пакета
rpm -qf /usr/sbin/nginx             # чей файл
rpm -V nginx                        # проверить целостность
```

### Ключевые файлы

| Файл | Описание |
|------|----------|
| `/etc/apt/sources.list` | Основные репозитории Debian |
| `/etc/apt/sources.list.d/` | Дополнительные репозитории |
| `/etc/apt/keyrings/` | GPG ключи репозиториев |
| `/etc/apt/preferences.d/` | Pinning правила |
| `/var/cache/apt/archives/` | Кэш скачанных .deb файлов |
| `/var/lib/dpkg/status` | База установленных пакетов |
| `/var/log/apt/history.log` | История установок |
| `/var/log/dpkg.log` | Полный лог dpkg операций |
| `/etc/yum.repos.d/` | Репозитории RHEL |

---

*Пакетные менеджеры · DevOps Middle+*
