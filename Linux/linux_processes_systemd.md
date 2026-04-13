# Процессы, systemd, cgroups, ulimits
> DevOps Middle — техническое руководство с разбором реальных кейсов

---

## Содержание

1. [Mental model — системная картина](#1-mental-model--системная-картина)
2. [Процессы — модель и состояния](#2-процессы--модель-и-состояния)
3. [Файловые дескрипторы и сокеты](#3-файловые-дескрипторы-и-сокеты)
4. [Load Average — правильное понимание](#4-load-average--правильное-понимание)
5. [Сигналы](#5-сигналы)
6. [systemd — устройство и отладка](#6-systemd--устройство-и-отладка)
7. [cgroups vs ulimits](#7-cgroups-vs-ulimits)
8. [Fork bomb и защита через nproc](#8-fork-bomb-и-защита-через-nproc)
9. [Анти-паттерны](#9-анти-паттерны)
10. [Универсальный алгоритм дебага](#10-универсальный-алгоритм-дебага)
11. [Реальный дебаг — кейсы](#11-реальный-дебаг--кейсы)
12. [Вопросы на собесе](#12-вопросы-на-собесе)
13. [Шпаргалка](#13-шпаргалка)

---

## 1. Mental model — системная картина

> Прежде чем лезть в команды — нужно держать в голове общую модель. Интервьюер отличает Middle от Junior именно по этому.

### Как Linux видит работающую систему

Всё что происходит на сервере — это три слоя:

```
┌─────────────────────────────────────────────────────┐
│  ПРИЛОЖЕНИЯ                                         │
│  nginx, postgres, python app, ...                   │
│  Каждое — это один или несколько процессов          │
├─────────────────────────────────────────────────────┤
│  SYSTEMD (PID 1)                                    │
│  Менеджер жизненного цикла всех сервисов            │
│  Запускает, следит, перезапускает, логирует         │
├─────────────────────────────────────────────────────┤
│  ЯДРО LINUX                                         │
│  Планировщик, cgroups, сигналы, fd, сеть            │
└─────────────────────────────────────────────────────┘
```

### Четыре ключевые абстракции

| Абстракция | Что это | Почему важно |
|-----------|---------|-------------|
| **Процесс** | Контейнер ресурсов: PID, память, fd, cgroup | Единица изоляции и управления |
| **Файловый дескриптор (fd)** | Число — ссылка на открытый ресурс | Файлы, сокеты, пайпы — всё через fd |
| **Сигнал** | Асинхронное уведомление процессу | Единственный способ общаться с процессом извне |
| **cgroup** | Группа процессов с общими лимитами ресурсов | Основа изоляции Docker, Kubernetes, systemd |

### Связи которые нужно держать в голове

```
Процесс
  ├── открывает fd → файлы, сокеты, пайпы (всё одно и то же)
  ├── принадлежит cgroup → ядро ограничивает CPU/память
  ├── имеет ulimits → ядро ограничивает fd, nproc
  └── управляется systemd → unit файл, journald, автоперезапуск

fd лимит (LimitNOFILE) ограничивает одновременно:
  ├── открытые файлы
  ├── сетевые соединения (каждое = fd)
  └── unix-сокеты

systemd при остановке сервиса:
  └── убивает всю cgroup → гарантированно умирают ВСЕ дочерние процессы
```

### Мышление при инциденте

Когда что-то сломалось — двигаемся по слоям сверху вниз:

```
1. СИМПТОМ         — что наблюдаем? (сервис упал / тормозит / не принимает соединения)
       ↓
2. СЛОЙ            — это приложение, systemd или ядро?
       ↓
3. МЕТРИКА         — load average, состояние D, OOM, fd limit?
       ↓
4. ИНСТРУМЕНТ      — journalctl, strace, iostat, lsof, /proc?
       ↓
5. ПРИЧИНА + ФИКС
```

---

## 2. Процессы — модель и состояния

> **Суть:** процесс — запущенная программа с собственным PID, памятью, fd и состоянием. Каждый процесс кроме PID 1 имеет родителя. Если PID 1 (systemd) падает — система падает.

### Ключевые атрибуты

| Атрибут | Где смотреть | Описание |
|---------|-------------|----------|
| `PID` | `ps`, `/proc/<PID>/status` | Уникальный идентификатор |
| `PPID` | `ps -o ppid`, `pstree -p` | Родительский процесс |
| `UID/GID` | `ps -o uid,gid` | От чьего имени запущен |
| `State` | `ps -o stat` | Текущее состояние |
| `Nice` | `ps -o ni`, `top` | Приоритет планировщика (-20 до +19) |
| `cgroup` | `/proc/<PID>/cgroup` | К какой cgroup принадлежит |
| `limits` | `/proc/<PID>/limits` | Текущие ulimits процесса |
| `wchan` | `/proc/<PID>/wchan` | В каком kernel call ждёт (если D) |

### Состояния процессов

| Символ | Название | Ключевой факт |
|--------|----------|---------------|
| `R` | Running / Runnable | Выполняется или стоит в очереди планировщика |
| `S` | Sleeping | Ждёт события — прерываемый, реагирует на сигналы |
| `D` | Disk Sleep | Ждёт I/O — **нельзя убить даже `kill -9`** |
| `Z` | Zombie | Умер, ждёт `wait()` от родителя |
| `T` | Stopped | Заморожен сигналом `SIGSTOP` |
| `I` | Idle | Idle kernel thread (ядро 4.14+) |

Дополнительные флаги в колонке `STAT`:

| Флаг | Смысл |
|------|-------|
| `s` | Session leader (например `bash`) |
| `l` | Многопоточный |
| `+` | Foreground процесс |
| `<` | Высокий приоритет (nice < 0) |
| `N` | Низкий приоритет (nice > 0) |

```bash
ps -eo pid,ppid,stat,ni,cmd --sort=stat   # все процессы со статусами
ps aux | awk '$8 == "Z"'                  # зомби
ps aux | awk '$8 ~ /^D/'                  # ждут I/O — признак проблем с диском/NFS
```

### Zombie

Зомби завершился, но родитель не вызвал `wait()`. Не потребляет CPU/память, но занимает PID. Тысячи зомби исчерпывают таблицу процессов — это всегда баг в родительском процессе.

```bash
# убить нельзя — убиваем родителя, systemd усыновит зомби и вызовет wait()
kill -9 $(ps -o ppid= -p <zombie_pid>)
```

### Состояние D

Процесс держит блокировку ядра, не реагирует ни на какие сигналы. Только устранение причины I/O зависания помогает.

```bash
cat /proc/<PID>/wchan    # в каком kernel call завис
dmesg | tail -30         # ошибки диска или NFS
smartctl -a /dev/sda     # здоровье диска
```

### OOM Killer

Когда RAM исчерпана — ядро убивает процесс с наибольшим `oom_score`.

```bash
cat /proc/<PID>/oom_score              # текущий score (0–1000)
echo -1000 > /proc/<PID>/oom_score_adj # защитить от OOM Killer
journalctl -k | grep -i "out of memory\|killed process"  # был ли OOM?
```

```ini
# в unit файле
OOMScoreAdjust=-500
```

---

## 3. Файловые дескрипторы и сокеты

> **Суть:** в Linux всё есть файл — и это не метафора. Файл, сетевое соединение, unix-сокет, пайп — всё открывается одинаково и даёт в ответ **число (fd)**. Именно поэтому лимит `LimitNOFILE` влияет на количество сетевых соединений.

### fd — это просто число

Когда процесс открывает любой ресурс — ядро возвращает ему целое число:

```
Процесс открывает...           получает fd
─────────────────────────────────────────────
/var/log/nginx/access.log  →   3
TCP соединение 1.2.3.4:80  →   4
TCP соединение 1.2.3.5:80  →   5
Unix-сокет /tmp/app.sock   →   6
Пайп к другому процессу    →   7
```

Три стандартных fd есть у каждого процесса с рождения:

| fd | Имя | Что это |
|----|-----|---------|
| `0` | stdin | Стандартный ввод |
| `1` | stdout | Стандартный вывод |
| `2` | stderr | Стандартный вывод ошибок |

### Почему это важно для DevOps

Каждое входящее соединение к nginx — это fd. Если nginx обслуживает 10 000 одновременных соединений — у него открыто минимум 10 000 fd. При дефолтном лимите `nofile=1024` nginx упадёт с ошибкой `Too many open files` задолго до реальной нагрузки.

```
LimitNOFILE ограничивает СУММАРНО:
  открытые файлы + TCP соединения + unix-сокеты + пайпы
  = всё что является fd
```

### Диагностика fd

```bash
# сколько fd открыто у процесса прямо сейчас
ls /proc/<PID>/fd | wc -l

# какие именно fd открыты
lsof -p <PID>

# посмотреть лимит процесса
cat /proc/<PID>/limits | grep "open files"

# удалённые файлы с открытыми fd (место не освобождается!)
lsof | grep deleted

# системная статистика: открыто / лимит
cat /proc/sys/fs/file-nr
# 9312  0  2097152
# ^^^^       ^^^^^^
# открыто    системный максимум
```

### Типичная ошибка и решение

```
[error] 1234#1234: accept() failed (24: Too many open files)
```

```bash
# 1. Проверить реальный лимит nginx
cat /proc/$(pgrep -o nginx)/limits | grep "open files"

# 2. Поднять через override
systemctl edit nginx
```
```ini
[Service]
LimitNOFILE=65536
```
```bash
# 3. Применить
systemctl daemon-reload && systemctl restart nginx

# 4. Убедиться что применилось (не доверяем — проверяем)
cat /proc/$(pgrep -o nginx)/limits | grep "open files"
```

### Очистить удалённый файл без перезапуска

```bash
# нашли: nginx держит удалённый лог размером 2GB
lsof | grep deleted
# nginx 1234 root 3w REG /var/log/nginx/access.log (deleted) 2.1G

# очищаем файл через fd — место освобождается немедленно
> /proc/1234/fd/3

# или graceful reload — nginx сам закроет и откроет логи
systemctl reload nginx
```

---

## 4. Load Average — правильное понимание

> **Суть:** Load Average — это **не загрузка CPU**. Это среднее количество процессов которые либо выполняются (R), либо ожидают ресурс — CPU или I/O (D) — за промежуток времени.

### Три числа

```bash
$ uptime
14:32:01 up 10 days, load average: 2.45, 1.87, 1.20
                                    1m    5m    15m
```

Нормально: Load Average ≤ количества CPU ядер (`nproc`).

```
Примеры для 4-ядерного сервера:

load: 1.00, 1.00, 1.00  → норма, 25% загрузка
load: 4.00, 4.00, 4.00  → 100%, всё занято, но не перегружено
load: 8.00, 6.00, 4.00  → нагрузка РАСТЁТ — разбираться сейчас
load: 4.00, 6.00, 8.00  → нагрузка СНИЖАЕТСЯ — проблема уходит
```

> ⚠️ Читать тренд **справа налево**: 15m → 5m → 1m. Если 1m > 15m — проблема нарастает.

### Load высокий, CPU свободен — это I/O

```bash
top          # %wa — процент времени CPU проведённого в ожидании I/O

# %wa высокий → проблема в дисках
iostat -x 1  # %util диска, await — среднее время ожидания
iotop -b -n 3

# %us/%sy высокий → CPU перегружен
ps -eo pid,pcpu,cmd --sort=-pcpu | head -10
```

**Пример разбора:**
```
load average: 12.00, 11.00, 10.00
%Cpu: 5.0 us, 2.0 sy, 82.0 wa

CPU почти свободен, 82% времени ждёт диск.
Идём в iostat → iotop → ищем процесс-виновника.
```

---

## 5. Сигналы

> **Суть:** сигнал — асинхронное уведомление процессу. Любой сигнал можно перехватить или проигнорировать, кроме `SIGKILL` и `SIGSTOP` — их обрабатывает только ядро.

### Основные сигналы

| № | Имя | Дефолт | Когда использовать |
|---|-----|--------|--------------------|
| 1 | `SIGHUP` | Завершение | Reload конфига демона без остановки |
| 2 | `SIGINT` | Завершение | Ctrl+C |
| 9 | `SIGKILL` | Завершение | Крайняя мера — нельзя перехватить |
| 15 | `SIGTERM` | Завершение | Graceful shutdown — всегда первым |
| 18 | `SIGCONT` | Продолжение | Разморозить после `SIGSTOP` |
| 19 | `SIGSTOP` | Остановка | Заморозить — нельзя перехватить |
| 10 | `SIGUSR1` | Зависит | nginx: reopen logs после logrotate |

### SIGTERM vs SIGKILL

**SIGTERM** — процесс получает сигнал и сам решает как завершиться. Правильное приложение делает graceful shutdown: завершает запросы, закрывает соединения с БД, сохраняет состояние.

**SIGKILL** — ядро уничтожает процесс немедленно. Риски: повреждённые файлы, незакоммиченные транзакции, брошенные соединения.

```bash
# правильная последовательность
kill <PID>       # 1. SIGTERM
sleep 10
kill -9 <PID>    # 2. SIGKILL — только если не завершился

pkill nginx            # SIGTERM по имени
kill -HUP $(pgrep nginx)   # reload конфига
kill -l                    # список всех сигналов
```

---

## 6. systemd — устройство и отладка

> **Суть:** systemd — PID 1, управляет жизненным циклом всех сервисов через unit файлы. Изолирует сервисы через cgroups — гарантирует что при остановке умирают все дочерние процессы.

### restart vs reload vs daemon-reload

Три разные операции с похожими названиями — путаница стоит часов:

| Команда | Что происходит | Даунтайм | Когда использовать |
|---------|---------------|----------|--------------------|
| `systemctl restart` | SIGTERM → ждёт `TimeoutStopSec` → SIGKILL → новый запуск | Да | Нужно полностью перезапустить процесс |
| `systemctl reload` | Отправляет SIGHUP — процесс перечитывает конфиг сам | Нет | Изменили конфиг, сервис поддерживает reload |
| `systemctl daemon-reload` | systemd перечитывает unit файлы с диска | Нет | Изменили сам unit файл (`*.service`) |

> ⚠️ **Главная ловушка:** изменили unit файл → сделали `systemctl restart` → изменения не применились. Нужно сначала `daemon-reload`, потом `restart`. systemd не перечитывает unit файлы автоматически.

```bash
# правильная последовательность после изменения unit файла
systemctl edit nginx          # изменили override.conf
systemctl daemon-reload       # systemd перечитал файл с диска
systemctl restart nginx       # процесс перезапустился с новыми настройками

# проверить поддерживает ли сервис reload
systemctl cat nginx | grep ExecReload
# если ExecReload= есть — reload работает
# если нет — reload упадёт с ошибкой
```

### systemd exit codes — расшифровка причин падения

Первое что смотрим после падения сервиса:

```bash
journalctl -u myapi -b 0 | grep -E "Main process|code=|status="
# или
systemctl status myapi | grep -A3 "Active:"
```

| Что видим | Смысл | Куда копать |
|-----------|-------|-------------|
| `code=exited, status=0` | Чистое завершение (код 0) | Кто остановил? Logrotate? Скрипт? |
| `code=exited, status=1` | Ненулевой код выхода | Баг в приложении — смотрим stderr |
| `code=exited, status=2` | Ошибка конфига / неверные аргументы | Проверить конфиг, запустить вручную |
| `code=killed, status=9/KILL` | SIGKILL — убил ядро или systemd | OOM? `TimeoutStopSec` истёк? |
| `code=killed, status=6/ABRT` | Аварийное завершение (assert, abort) | Core dump, смотреть backtrace |
| `code=killed, status=11/SEGV` | Segmentation fault | Баг в коде или нехватка памяти |
| `code=killed, status=15/TERM` | SIGTERM — кто-то попросил завершиться | Кто отправил? Другой скрипт? |

**Практический разбор:**

```bash
# статус=9/KILL — первым делом проверяем OOM
journalctl -k -b 0 | grep -i "out of memory\|killed process"

# статус=1 — смотрим что напечатал процесс перед смертью
journalctl -u myapi -b 0 -n 50

# чистое завершение (статус=0) — проверяем кто мог остановить
journalctl --since "02:55" --until "03:05" | grep -i "stop\|kill\|myapi"
```

### Анатомия unit файла

```ini
[Unit]
Description=My Production API
After=network-online.target postgresql.service   # порядок, не зависимость
Requires=postgresql.service                      # жёсткая зависимость
StartLimitIntervalSec=120                        # не более 5 перезапусков
StartLimitBurst=5                                # за 2 минуты

[Service]
# simple  — ExecStart это основной процесс (дефолт)
# forking — процесс делает fork, родитель завершается (старые демоны)
# notify  — процесс сам сообщает systemd когда готов (nginx, postgres)
# oneshot — выполняется и завершается (скрипты)
Type=notify

User=myapi
Group=myapi
WorkingDirectory=/opt/myapi
EnvironmentFile=/etc/myapi/env

ExecStartPre=/opt/myapi/venv/bin/python manage.py migrate
ExecStart=/opt/myapi/venv/bin/gunicorn --workers 4 --bind 0.0.0.0:8000 myapi.wsgi
ExecReload=/bin/kill -HUP $MAINPID

Restart=on-failure
RestartSec=10s
TimeoutStopSec=30      # после — SIGKILL всем процессам cgroup

# Ресурсы через cgroups
CPUQuota=200%
MemoryHigh=800M        # мягкий лимит — throttle
MemoryMax=1G           # жёсткий лимит — OOM kill
LimitNOFILE=65536

# Hardening
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ReadWritePaths=/var/log/myapi /var/lib/myapi
OOMScoreAdjust=-100

[Install]
WantedBy=multi-user.target
```

### Основные команды

```bash
systemctl status <svc>             # статус + последние логи
systemctl start|stop|restart <svc>
systemctl reload <svc>             # SIGHUP, без даунтайма
systemctl enable --now <svc>       # автозапуск + запустить сейчас
systemctl edit <svc>               # override (сохранится при обновлении пакета)
systemctl daemon-reload            # после любых изменений unit файлов
systemctl list-units --state=failed
systemctl show <svc> | grep -E "^(CPU|Memory|Limit|NRestarts)"
```

### systemd debugging — алгоритм

```bash
# Шаг 1: код завершения — сужаем причину
systemctl status myapi
journalctl -u myapi -b 0 | grep -E "code=|status="

# Шаг 2: полные логи
journalctl -u myapi -b 0 --no-pager

# Шаг 3: логи предыдущего запуска (если уже перезапустился)
journalctl -u myapi -b -1

# Шаг 4: OOM?
journalctl -k -b 0 | grep -i "out of memory\|killed process"

# Шаг 5: история перезапусков
systemctl show myapi | grep NRestarts

# Шаг 6: воспроизвести вручную от того же пользователя
sudo -u myapi /opt/myapi/venv/bin/gunicorn --workers 4 myapi.wsgi
# часто сразу видна ошибка которую systemd скрывает

# Шаг 7: проблемы загрузки системы
systemd-analyze blame
systemd-analyze verify myapi.service   # проверить синтаксис unit файла
```

### journalctl

```bash
journalctl -u <svc> -f              # follow
journalctl -u <svc> -b -1           # предыдущая загрузка
journalctl -u <svc> -p err          # только ошибки
journalctl -u <svc> -o json-pretty  # JSON формат
journalctl -k                       # только kernel messages
journalctl --since "10 min ago"
journalctl --vacuum-time=7d         # удалить логи старше 7 дней
```

### Таймеры вместо cron

```ini
# backup.timer
[Timer]
OnCalendar=*-*-* 02:00:00
Persistent=true             # запустить если система была выключена
RandomizedDelaySec=300      # разброс для множества серверов
[Install]
WantedBy=timers.target
```

```bash
systemctl list-timers --all   # все таймеры и время следующего запуска
```

---

## 7. cgroups vs ulimits

> **Суть:** оба ограничивают ресурсы, но на разных уровнях. `limits.conf` не работает для systemd сервисов — это самая частая ловушка.

### Сравнительная таблица

| Критерий | cgroups | ulimits |
|----------|---------|---------|
| Уровень | Ядро — группа процессов | Ядро через PAM — один процесс и потомки |
| Гранулярность | Все процессы сервиса как группа | Каждый процесс отдельно |
| Ресурсы | CPU, память, I/O, устройства | fd, процессы, стек, core dump |
| Иерархия | Да — дочерние наследуют | Нет |
| Изменение на лету | Да, без перезапуска | Soft: да. Hard: только root |
| В systemd | `CPUQuota=`, `MemoryMax=` | `LimitNOFILE=`, `LimitNPROC=` |
| Для контейнеров | Основа Docker и Kubernetes | Не используется |

> ⚠️ `limits.conf` работает через PAM только для **интерактивных сессий**. systemd запускает сервисы без PAM — `limits.conf` игнорируется. Для сервисов — только `Limit*=` в unit файле.

### cgroups — управление ресурсами

```bash
# применить лимит БЕЗ перезапуска
systemctl set-property nginx.service MemoryMax=512M
systemctl set-property nginx.service CPUQuota=50%

# мониторинг
systemd-cgtop       # потребление по сервисам в реальном времени
systemd-cgls        # дерево cgroup
```

```ini
[Service]
CPUWeight=100        # вес при конкуренции (1–10000, дефолт 100)
CPUQuota=50%         # жёсткий лимит
MemoryMin=128M       # гарантированный минимум
MemoryHigh=400M      # мягкий — throttle при превышении
MemoryMax=512M       # жёсткий — OOM kill при превышении
MemorySwapMax=0      # запретить swap
IOReadBandwidthMax=/dev/sda 100M
IOWriteBandwidthMax=/dev/sda 50M
TasksMax=200
```

### ulimits — лимиты процессов

```bash
ulimit -a                        # все лимиты текущей сессии
cat /proc/<PID>/limits           # реальные лимиты запущенного процесса
```

| Параметр | Дефолт | Рекомендация для сервера |
|----------|--------|--------------------------|
| `nofile` | 1024 | 65536+ |
| `nproc` | 1024–4096 | 65536+ |
| `memlock` | 64 KB | `unlimited` для Elasticsearch, Redis |
| `core` | 0 | `unlimited` для отладки |

```ini
# В unit файле — единственный правильный способ для systemd
[Service]
LimitNOFILE=65536
LimitNPROC=4096
LimitMEMLOCK=infinity
LimitCORE=infinity
```

### cgroups v1 vs v2

| | v1 | v2 |
|-|----|----|
| Иерархия | Отдельная для каждого контроллера | Единая для всех |
| Делегирование | Ограничено | Полное — нужно для rootless контейнеров |
| Дефолт | До Debian 10 | Debian 11+, Ubuntu 22.04+ |
| Docker | До 20.10 | С Docker 20.10+ |

```bash
stat -fc %T /sys/fs/cgroup/   # cgroup2fs = v2 / tmpfs = v1
```

---

## 8. Fork bomb и защита через nproc

> **Суть:** fork bomb исчерпывает таблицу процессов за секунды. Без лимита на количество процессов система перестаёт отвечать.

```bash
# классическая bash fork bomb
# НИКОГДА не запускать на продакшене
:(){ :|:& };:
# функция : вызывает себя дважды → 2 → 4 → 8 → 16 → ... → падение
```

### Защита

```bash
# /etc/security/limits.d/nproc.conf
www-data  soft  nproc  512
www-data  hard  nproc  1024
*         soft  nproc  4096
```

```ini
# В unit файле — предпочтительно для сервисов
[Service]
TasksMax=512
```

**Почему `TasksMax` лучше `nproc` для сервисов:**

`nproc` — лимит на **пользователя**: все сервисы от `www-data` делят один лимит.
`TasksMax` — лимит на **cgroup конкретного сервиса**: изолирует независимо от пользователя.

### Восстановление

```bash
pkill -9 -u attackuser                              # убить все процессы пользователя
systemctl kill --kill-who=all --signal=SIGKILL svc  # убить всю cgroup сервиса
```

---

## 9. Анти-паттерны

> Знание антипаттернов — признак зрелого инженера. На собесе часто спрашивают: «что здесь не так?»

### `kill -9` как первый инструмент

```bash
# ❌
kill -9 $(pgrep nginx)

# ✅
systemctl stop nginx    # SIGTERM → TimeoutStopSec → SIGKILL
```

`kill -9` на nginx в момент обработки запросов = обрыв соединений. На PostgreSQL = возможное повреждение данных.

---

### Не делать `daemon-reload` после изменения unit файла

```bash
# ❌
systemctl edit nginx
systemctl restart nginx    # изменения не применились!

# ✅
systemctl edit nginx
systemctl daemon-reload    # systemd перечитал файл
systemctl restart nginx
```

---

### Читать только одно число load average

```bash
# ❌ — смотрим только последнее число
# ✅
uptime   # читаем тренд: load: 8.00, 4.00, 2.00 → нагрузка РАСТЁТ
```

---

### `chmod -R 777` или запуск сервиса от root

```bash
# ❌
chmod -R 777 /var/www/

# ✅
find /var/www -type d -exec chmod 755 {} \;
find /var/www -type f -exec chmod 644 {} \;
# в unit файле: User=www-data, Group=www-data
```

---

### Редактировать системные unit файлы напрямую

```bash
# ❌ — перезапишется при обновлении пакета
nano /lib/systemd/system/nginx.service

# ✅ — override сохранится при обновлении
systemctl edit nginx
# → /etc/systemd/system/nginx.service.d/override.conf
```

---

### `Restart=always` без `StartLimitBurst`

```ini
# ❌ — бесконечный цикл перезапусков, спам логов
Restart=always

# ✅
Restart=on-failure
RestartSec=10s
StartLimitIntervalSec=120
StartLimitBurst=5    # после 5 попыток — failed, нужно ручное вмешательство
```

---

### Не настраивать `TimeoutStopSec`

```ini
# ❌ — дефолт 90 секунд, деплой висит почти 2 минуты при каждой остановке
# ✅
TimeoutStopSec=30    # знаем сколько нужно приложению для graceful shutdown
```

---

## 10. Универсальный алгоритм дебага

> Алгоритм который работает для любого инцидента. На собесе произнести его вслух — уже Middle.

```
СИМПТОМ
  Что сломалось? Сервис упал / тормозит / не принимает соединения / диск полный
        │
        ▼
СЛОЙ
  Приложение? systemd? Ядро (OOM, I/O)?
        │
        ▼
ВРЕМЕННАЯ МЕТКА
  Когда началось? Что изменилось в это время?
  journalctl --since / git log / deployment history
        │
        ▼
КОД ЗАВЕРШЕНИЯ (если сервис упал)
  code=exited status=1    → баг в приложении
  code=killed status=9    → OOM или TimeoutStopSec
  code=killed status=11   → SIGSEGV
        │
        ▼
МЕТРИКА ПОД СИМПТОМ
  Упал          → journalctl -u svc -b 0
  Тормозит      → top / iostat / vmstat
  Нет соед.     → lsof fd count / ss -s
  Диск полный   → lsof | grep deleted / du -sh
  Load высокий  → %wa в top → iostat → iotop
        │
        ▼
ПРИЧИНА → ФИКС → ПРОВЕРКА
  Всегда проверять что фикс применился
  (cat /proc/<PID>/limits, systemctl show, ss -s)
```

### Практические маршруты

| Симптом | Первая команда | Если не помогло |
|---------|---------------|-----------------|
| Сервис упал | `systemctl status svc` | `journalctl -u svc -b -1` |
| Сервис падает циклично | `systemctl show svc \| grep NRestarts` | `journalctl -k \| grep oom` |
| Тормозит всё | `uptime && nproc` | `top → %wa → iostat` |
| Нет места | `df -h` | `lsof \| grep deleted` |
| Нет соединений | `ss -s` | `cat /proc/$(pgrep -o svc)/limits` |
| Процесс завис | `cat /proc/<PID>/wchan` | `strace -p <PID>` |

---

## 11. Реальный дебаг — кейсы

> Здесь не команды — а **мышление**: как рассуждает DevOps при инциденте.

---

### Кейс 1: «Сервис упал ночью, утром никто не знает почему»

```bash
# Шаг 1: когда и как завершился
systemctl show myapi | grep -E "NRestarts|ActiveEnterTimestamp"
journalctl -u myapi -b 0 | grep -E "code=|status="

# code=killed status=9 → SIGKILL
# Шаг 2: OOM?
journalctl -k -b 0 | grep -i "out of memory\|killed process"
# Если нашли — поднять MemoryMax или MemoryHigh в unit файле

# code=exited status=1 → баг в приложении
# Шаг 3: что напечатал перед смертью
journalctl -u myapi --since "03:00" --until "03:10"

# code=exited status=0 → кто-то остановил
# Шаг 4: кто мог остановить
journalctl --since "02:55" --until "03:05" | grep -i "stop\|myapi"
```

---

### Кейс 2: «Диск заполнен, но `du` и `df` не сходятся»

```bash
lsof | grep deleted
# nginx 1234 root 3w REG /var/log/nginx/access.log (deleted) 2.1G

# Решение без перезапуска
> /proc/1234/fd/3           # очистить файл через fd
# или
systemctl reload nginx      # nginx сам закроет и откроет логи
```

Корень проблемы: logrotate удаляет файл но не делает reload nginx. Правильно:

```bash
# /etc/logrotate.d/nginx
postrotate
    systemctl reload nginx
endscript
```

---

### Кейс 3: «Высокий load average, но CPU почти свободен»

```bash
# load: 15.0 на 4-ядерном сервере, CPU usage 20%
top   # %wa = 82% → процессы ждут диск

ps aux | awk '$8 ~ /^D/'   # кто в состоянии D?
iostat -x 1                 # %util диска близко к 100%
iotop -b -n 3               # кто грузит диск

# нашли процесс — смотрим что он делает
cat /proc/<PID>/wchan
strace -p <PID> -e trace=read,write,open
```

---

### Кейс 4: «Nginx перестал принимать соединения»

```bash
# fd исчерпаны?
cat /proc/$(pgrep -o nginx)/limits | grep "open files"
ls /proc/$(pgrep -o nginx)/fd | wc -l
# Max open files: 1024, открыто: 1023 → нашли

# фикс
systemctl edit nginx
# [Service]
# LimitNOFILE=65536
systemctl daemon-reload && systemctl restart nginx

# убедиться
cat /proc/$(pgrep -o nginx)/limits | grep "open files"
# Max open files: 65536 ← применилось
```

---

### Кейс 5: «После деплоя старые процессы ещё живут»

```bash
systemctl status myapi   # смотрим секцию CGroup

# принудительно убить всю cgroup
systemctl kill --kill-who=all myapi.service

# частая причина: Type=forking без PIDFile=
# systemd не знает главный PID → не контролирует дочерние
# фикс: правильно настроить Type= и PIDFile=, или перейти на Type=notify
```

---

## 12. Вопросы на собесе

### Базовый уровень

**В: Что такое Load Average?**
> О: Среднее количество процессов в очереди на выполнение (R) или ожидающих I/O (D) за 1, 5 и 15 минут. Это не загрузка CPU. Нормально когда ≤ количества ядер. Важен тренд: 1m > 15m — нагрузка нарастает.

**В: Чем `systemctl restart` отличается от `reload` и `daemon-reload`?**
> О: `restart` — полная остановка и новый запуск, есть даунтайм. `reload` — SIGHUP процессу, он перечитывает конфиг без остановки, даунтайма нет. `daemon-reload` — systemd перечитывает unit файлы с диска, на запущенные процессы не влияет. После изменения unit файла нужно `daemon-reload`, потом `restart`.

**В: Что такое зомби и чем опасны тысячи зомби?**
> О: Завершился, но родитель не вызвал `wait()`. Не потребляет ресурсы, но занимает PID. Тысячи исчерпывают таблицу процессов — новые создать невозможно.

---

### Middle уровень

**В: Load average 20 на 8-ядерном сервере, CPU 15%. Что происходит?**
> О: Процессы ждут I/O — состояние D. CPU свободен, но диск не справляется. Смотрим `top %wa`, `iostat -x`, `iotop`. Причины: перегруженный диск, умирающий HDD, зависший NFS.

**В: Сервис завершился с `code=killed, status=9/KILL`. Что это значит?**
> О: Процесс получил SIGKILL — либо от OOM Killer (память исчерпана), либо истёк `TimeoutStopSec` при остановке. Проверяем: `journalctl -k | grep "out of memory"`. Если OOM — поднять `MemoryMax` или оптимизировать приложение.

**В: Процесс в состоянии D, `kill -9` не помогает. Действия?**
> О: Состояние D нельзя убить — процесс держит блокировку ядра. Диагностика: `cat /proc/<PID>/wchan`, `dmesg | tail`, проверить NFS. Решение — устранить причину I/O зависания. Крайний случай — перезагрузка.

**В: Почему `LimitNOFILE` в `limits.conf` не применился к nginx?**
> О: `limits.conf` работает через PAM только для интерактивных сессий. systemd запускает сервисы напрямую, без PAM. Нужно `LimitNOFILE=65536` в unit файле через `systemctl edit nginx`.

**В: Почему лимит `LimitNOFILE` влияет на количество сетевых соединений?**
> О: В Linux каждое TCP соединение — это файловый дескриптор. Файлы, сокеты, пайпы — всё через fd. Лимит `nofile` ограничивает их суммарное количество. При дефолте 1024 nginx упадёт с `Too many open files` при нагрузке.

**В: Чем cgroups отличаются от ulimits?**
> О: ulimits — лимиты на отдельный процесс через PAM, не работают для systemd. cgroups — иерархические группы процессов с лимитами на всю группу, управляются ядром напрямую. Для сервисов cgroups предпочтительнее: гарантируют изоляцию независимо от количества дочерних процессов.

**В: Как systemd гарантирует смерть всех дочерних процессов при остановке?**
> О: Помещает все процессы сервиса в одну cgroup. При остановке — SIGTERM всем в cgroup, ждёт `TimeoutStopSec`, затем SIGKILL всем оставшимся. Процесс не может выйти из cgroup — это гарантия ядра.

**В: Что такое fork bomb и чем `TasksMax` лучше `nproc` для защиты?**
> О: Fork bomb — процесс бесконечно клонирует себя, исчерпывая таблицу процессов. `nproc` — лимит на пользователя, все сервисы от одного пользователя делят его. `TasksMax` — лимит на cgroup конкретного сервиса, независимо от пользователя.

---

## 13. Шпаргалка

### Диагностика за 60 секунд

```bash
uptime && nproc                              # load average vs ядра
ps aux | awk '$8 ~ /^[DZ]/ {print $0}'      # проблемные состояния D и Z
free -h                                      # память и swap
iostat -x 1 2 | tail -5                      # нагрузка на диск
systemctl list-units --state=failed          # упавшие сервисы
journalctl -p err --since "1 hour ago"       # системные ошибки
```

### Процессы и fd

| Команда | Описание |
|---------|----------|
| `ps -eo pid,ppid,stat,ni,cmd --sort=stat` | Все процессы со статусами |
| `lsof -p <PID>` | Открытые файлы и сокеты процесса |
| `lsof \| grep deleted` | Удалённые файлы с открытыми fd |
| `ls /proc/<PID>/fd \| wc -l` | Количество открытых fd |
| `cat /proc/<PID>/limits` | Реальные ulimits процесса |
| `cat /proc/<PID>/wchan` | В каком kernel вызове завис |
| `cat /proc/<PID>/oom_score` | Приоритет OOM Killer |
| `cat /proc/sys/fs/file-nr` | Системная статистика fd |
| `strace -p <PID>` | Системные вызовы в реальном времени |

### Сигналы

| Команда | Описание |
|---------|----------|
| `kill <PID>` | SIGTERM — всегда первый |
| `kill -9 <PID>` | SIGKILL — только крайний случай |
| `kill -HUP <PID>` | SIGHUP — reload конфига |
| `pkill -u <user>` | Убить все процессы пользователя |
| `kill -l` | Список сигналов с номерами |

### systemd

| Команда | Описание |
|---------|----------|
| `systemctl status <svc>` | Статус + код завершения + логи |
| `systemctl reload <svc>` | SIGHUP — без даунтайма |
| `systemctl edit <svc>` | Override — сохранится при обновлении пакета |
| `systemctl daemon-reload` | Обязательно после изменений unit файла |
| `systemctl show <svc> \| grep -E "CPU\|Memory\|Limit\|NRestarts"` | Лимиты и статистика |
| `systemd-analyze blame` | Что тормозит загрузку |
| `systemd-analyze verify <svc>.service` | Проверить синтаксис |

### journalctl

| Команда | Описание |
|---------|----------|
| `journalctl -u <svc> -f` | Follow |
| `journalctl -u <svc> -b -1` | Предыдущая загрузка |
| `journalctl -u <svc> -p err` | Только ошибки |
| `journalctl -k \| grep -i oom` | OOM события |
| `journalctl -u <svc> \| grep -E "code=\|status="` | Причина завершения |

### cgroups / ulimits

| Команда | Описание |
|---------|----------|
| `systemd-cgtop` | Потребление ресурсов по сервисам |
| `systemd-cgls` | Дерево cgroup |
| `systemctl set-property <svc> MemoryMax=512M` | Лимит памяти без рестарта |
| `stat -fc %T /sys/fs/cgroup/` | Версия cgroups (v1/v2) |
| `ulimit -a` | Лимиты текущей сессии |

---

*Процессы, systemd, cgroups, ulimits · DevOps Middle*
