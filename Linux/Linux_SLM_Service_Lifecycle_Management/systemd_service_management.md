# systemd — управление сервисами
> Углублённая лекция · DevOps Middle
>
> Выделено из общего документа `linux_processes_systemd.md`. Журналирование через `journalctl` входит сюда же — это часть инструментария systemd, а не отдельная тема логирования.

---

## Содержание

1. [systemd — устройство, отладка, unit файл](#1-systemd)
2. [Анти-паттерны](#2-анти-паттерны)

---

## 1. systemd

**systemd** — это PID 1, первый процесс который запускает ядро после загрузки. Он управляет **жизненным циклом всех сервисов**: запускает их в правильном порядке, следит за состоянием, перезапускает при падении, собирает логи, изолирует через cgroups.

До systemd (SysV init) каждый сервис был отдельным bash-скриптом в `/etc/init.d/` — громоздко, нет параллельного запуска, нет зависимостей, нет автоперезапуска.

> При остановке сервиса: SIGTERM → всем процессам cgroup → ждёт `TimeoutStopSec` (сколько секунд дать процессу на graceful shutdown, дефолт 90) → SIGKILL всем оставшимся. Ни один дочерний процесс не выживет — это гарантия ядра.

### restart vs reload vs daemon-reload

Три операции с похожими названиями — их путают даже опытные люди:

| Команда | Что происходит | Даунтайм | Когда использовать |
|---------|---------------|----------|--------------------|
| `systemctl restart` | SIGTERM → ждёт `TimeoutStopSec` → SIGKILL → запуск нового процесса | Да | Нужен полный перезапуск процесса |
| `systemctl reload` | Отправляет SIGHUP — процесс сам перечитывает конфиг | Нет | Изменили конфиг приложения (nginx.conf, etc.) |
| `systemctl daemon-reload` | systemd перечитывает unit файлы с диска | Нет | Изменили сам `*.service` файл |

> ⚠️ **Главная ловушка:** изменили unit файл → `systemctl restart` → изменения **не** применились. systemd кэширует unit файлы в памяти и не перечитывает их автоматически. Правильная последовательность: `daemon-reload` → `restart`.

```bash
# проверить поддерживает ли сервис reload
systemctl cat nginx | grep ExecReload
# ExecReload=/bin/kill -HUP $MAINPID  ← поддерживает
# (если строки нет — reload не работает, нужен restart)

# правильная последовательность при изменении unit файла
systemctl edit nginx          # изменяем override.conf
systemctl daemon-reload       # systemd перечитывает файл с диска
systemctl restart nginx       # процесс перезапускается с новыми настройками
```

### exit codes — расшифровка причин падения

Это первое что нужно смотреть после падения сервиса. По коду завершения можно сразу понять куда копать:

```bash
systemctl status myapi.service
# Active: failed (Result: exit-code) since ...
#   Main PID: 1234 (code=exited, status=1/FAILURE)

journalctl -u myapi -b 0 | grep -E "code=|status="
```

| Что видим | Смысл | Куда копать |
|-----------|-------|-------------|
| `code=exited, status=0` | Чистое завершение (процесс вышел сам с кодом 0) | Кто остановил? Скрипт? Logrotate? |
| `code=exited, status=1` | Ненулевой код выхода (ошибка в приложении) | Смотреть stderr: `journalctl -u svc -b 0 -n 50` |
| `code=exited, status=2` | Ошибка конфига / неверные аргументы | Запустить вручную — сразу увидим ошибку |
| `code=killed, status=9/KILL` | Получил SIGKILL | OOM? Истёк `TimeoutStopSec`? |
| `code=killed, status=11/SEGV` | Segmentation fault | Баг в коде или нехватка памяти |
| `code=killed, status=15/TERM` | Получил SIGTERM | Кто отправил? Другой скрипт? Деплой? |

```bash
# status=9/KILL — проверяем OOM в первую очередь
journalctl -k -b 0 | grep -i "out of memory\|killed process"

# status=1 — смотрим что напечатал перед смертью
journalctl -u myapi -b 0 -n 100

# status=0 — ищем кто мог остановить
journalctl --since "02:55" --until "03:05" | grep -i "stop\|myapi"
```

### Минимальный production unit файл

```ini
[Unit]
Description=My Production API
# After = порядок запуска, не жёсткая зависимость
After=network-online.target postgresql.service
# Requires = жёсткая зависимость: не запустимся если postgresql упал
Requires=postgresql.service
# Не более 5 перезапусков за 2 минуты — защита от бесконечного цикла
StartLimitIntervalSec=120
StartLimitBurst=5

[Service]
# Типы:
# simple  — ExecStart это основной процесс работающий в foreground, systemd следит за ним напрямую
# forking — процесс делает Double Fork и уходит в фон сам (legacy демоны, старый nginx/apache)
#           без этого типа systemd убьёт cgroup когда родитель завершится после первого fork
# notify  — процесс сам уведомляет systemd через sd_notify() когда готов (современный подход)
# oneshot — выполняется и завершается (скрипты, одноразовые задачи)
Type=notify

User=myapi
Group=myapi
WorkingDirectory=/opt/myapi
EnvironmentFile=/etc/myapi/env

ExecStart=/opt/myapi/venv/bin/gunicorn --workers 4 --bind 0.0.0.0:8000 myapi.wsgi
ExecReload=/bin/kill -HUP $MAINPID

# on-failure = перезапускать при ненулевом коде выхода, SIGKILL и timeout
Restart=on-failure
RestartSec=10s
TimeoutStopSec=30    # сколько секунд ждать после SIGTERM прежде чем отправить SIGKILL если процесс не завершился сам (дефолт 90)

# Ресурсы через cgroups
CPUQuota=200%           # максимум 2 ядра
MemoryHigh=800M         # мягкий лимит — throttle
MemoryMax=1G            # жёсткий лимит — OOM kill
LimitNOFILE=65536       # лимит fd (через ulimit, не cgroup)

# Hardening — изоляция
NoNewPrivileges=yes     # процесс не может получить новые привилегии
PrivateTmp=yes          # изолированный /tmp
ProtectSystem=strict    # /usr, /boot, /etc только для чтения
ReadWritePaths=/var/log/myapi /var/lib/myapi
OOMScoreAdjust=-100     # чуть меньше шансов быть убитым OOM Killer

[Install]
WantedBy=multi-user.target
```

### Targets — чем заменили runlevels

**Target** — это не процесс и не сервис, а точка синхронизации в графе зависимостей: набор юнитов, которые должны быть активны одновременно. В SysV init были линейные runlevels (0–6), в systemd — граф, поэтому юниты внутри одного target стартуют параллельно, а не строго по очереди один за другим.

| SysV runlevel | systemd target | Что это |
|---|---|---|
| 0 | `poweroff.target` | Выключение |
| 1 | `rescue.target` | Однопользовательский режим, минимум сервисов |
| 3 | `multi-user.target` | Обычный сервер без GUI — то, что крутится на 99% продакшен-машин |
| 5 | `graphical.target` | Графическое окружение (наследует multi-user.target + добавляет display manager) |
| 6 | `reboot.target` | Перезагрузка |

```bash
systemctl get-default                    # какой target грузится по умолчанию
systemctl set-default multi-user.target  # сменить дефолтный target (правит симлинк, не сам юнит)
systemctl isolate rescue.target          # переключиться ПРЯМО СЕЙЧАС — остановит всё что не нужно rescue.target
                                          # осторожно на удалённой машине: можно потерять sshd и доступ
systemctl list-dependencies multi-user.target   # что реально тянет за собой этот target
```

Загрузка системы — это не последовательность скриптов как в SysV, а раскрутка графа зависимостей от одной точки:

```
default.target (симлинк → multi-user.target)
       │ Wants=
       ├──► sshd.service
       ├──► network-online.target
       │         │ Wants=
       │         └──► NetworkManager.service
       ├──► myapi.service ──── After= postgresql.service
       │                       (только порядок! без Requires= стартанёт и без неё)
       └──► postgresql.service
```

> Юниты без явной зависимости друг от друга стартуют **параллельно** — одна из причин почему systemd грузится быстрее SysV init, где каждый скрипт дожидался завершения предыдущего.

### Таймеры — замена cron

`.timer`-юнит запускает `.service`-юнит с тем же базовым именем по расписанию. В отличие от cron, таймер — полноправный systemd-юнит: виден в `journalctl`, может зависеть от других юнитов, и не теряет пропущенные запуски при выключенной машине.

```ini
# /etc/systemd/system/backup.timer
[Unit]
Description=Ежедневный бэкап БД в 3 ночи

[Timer]
OnCalendar=*-*-* 03:00:00     # синтаксис похож на cron, но читаемый
# OnBootSec=15min              # альтернатива: через 15 минут после загрузки системы
# OnUnitActiveSec=1h           # альтернатива: через час после последнего запуска (интервал, не время)
Persistent=true                # если машина была выключена в 3:00 — выполнится при следующей загрузке
RandomizedDelaySec=300         # случайная задержка до 5 минут — разносит нагрузку если таймер на многих машинах

[Install]
WantedBy=timers.target
```

```bash
systemctl enable --now backup.timer
systemctl list-timers --all          # когда сработает следующий раз, когда сработал последний
```

| | cron | systemd timer |
|---|---|---|
| Логи | отдельно настраивать (mail/syslog) | сразу в `journalctl -u backup.service` |
| Пропуск при выключенной машине | теряется | `Persistent=true` догоняет при следующей загрузке |
| Зависимость от других юнитов | нет | `After=`/`Requires=` как у любого юнита |
| Распределение нагрузки | вручную (`sleep $RANDOM`) | `RandomizedDelaySec=` |

### Socket activation — запуск по требованию

Идея: systemd сам слушает порт или unix-socket **вместо** сервиса, и запускает сервис только когда приходит первое подключение. Уже открытый файловый дескриптор сокета передаётся процессу при старте.

Зачем это нужно:
- **Быстрее загрузка** — не запускать сервис, который прямо сейчас никому не нужен
- **Снятие проблемы порядка запуска** — клиент может подключаться к сокету сразу после загрузки, даже если сам сервис ещё не поднялся: подключение просто встанет в очередь, а не получит `connection refused`
- **Привилегированные порты без root в сервисе** — systemd (root) биндится на порт 80, а сам процесс сервиса запускается от непривилегированного юзера и получает уже готовый fd — ему не нужен `CAP_NET_BIND_SERVICE`

```ini
# myapp.socket
[Socket]
ListenStream=8080

[Install]
WantedBy=sockets.target
```

```ini
# myapp.service — fd уже передан через LISTEN_FDS, отдельный bind в коде не нужен
[Service]
ExecStart=/opt/myapp/server
```

> Чтобы это реально сработало, само приложение должно уметь забрать переданный fd через `sd_listen_fds()` (или systemd-совместимую библиотеку) — это не происходит «само собой» для любой программы. Из коробки поддерживают сравнительно немногие демоны (например `sshd`, `cups`); для своих сервисов это нужно явно закладывать в код.

```bash
systemctl list-sockets        # какие сокеты сейчас слушает systemd
```

### Основные команды

```bash
systemctl status <svc>               # статус + последние логи + код завершения
systemctl start|stop|restart <svc>
systemctl reload <svc>               # SIGHUP без даунтайма
systemctl enable --now <svc>         # включить автозапуск + запустить сейчас
systemctl disable --now <svc>        # отключить автозапуск + остановить
systemctl edit <svc>                 # создать override.conf (сохранится при обновлении пакета)
systemctl daemon-reload              # ОБЯЗАТЕЛЬНО после изменений unit файла
systemctl list-units --state=failed  # все упавшие сервисы
systemctl show <svc> | grep -E "^(CPU|Memory|Limit|NRestarts|ActiveEnter)"
systemd-analyze blame                # что тормозит загрузку системы
systemd-analyze verify <svc>.service # проверить синтаксис unit файла
```

### Debugging алгоритм

```bash
# Шаг 1: смотрим код завершения
systemctl status myapi
journalctl -u myapi -b 0 | grep -E "code=|status="

# Шаг 2: полные логи текущей загрузки
journalctl -u myapi -b 0 --no-pager

# Шаг 3: если сервис уже перезапустился — логи предыдущего запуска
journalctl -u myapi -b -1

# Шаг 4: OOM?
journalctl -k -b 0 | grep -i "out of memory\|killed process"

# Шаг 5: история перезапусков
systemctl show myapi | grep NRestarts

# Шаг 6: воспроизвести вручную — часто сразу видна ошибка которую systemd скрывает
sudo -u myapi /opt/myapi/venv/bin/gunicorn --workers 4 myapi.wsgi
```

### journalctl

```bash
journalctl -u <svc> -f              # follow (как tail -f)
journalctl -u <svc> -b -1           # логи предыдущей загрузки системы
journalctl -u <svc> -p err          # только ошибки
journalctl -u <svc> -o json-pretty  # JSON формат (удобно для парсинга)
journalctl -k | grep -i oom         # OOM события из ядра
journalctl --since "10 min ago"     # за последние 10 минут
journalctl --vacuum-time=7d         # удалить логи старше 7 дней
```

---

## 2. Анти-паттерны

### Не делать `daemon-reload` после изменения unit файла

```bash
# ❌  — изменения в unit файле не применились
systemctl edit nginx && systemctl restart nginx

# ✅
systemctl edit nginx
systemctl daemon-reload   # systemd перечитал файл
systemctl restart nginx
```

---

### `limits.conf` для systemd сервиса

```bash
# ❌ — systemd не использует PAM, limits.conf игнорируется
echo "nginx soft nofile 65536" >> /etc/security/limits.conf

# ✅
systemctl edit nginx
# [Service]
# LimitNOFILE=65536

# и ОБЯЗАТЕЛЬНО проверить что применилось
cat /proc/$(pgrep -o nginx)/limits | grep "open files"
```

> Полное объяснение почему так — механизм PAM и две цепочки запуска — в файле `process_and_resource_management.md`, раздел «cgroups vs ulimits» → «Грабли №1: limits.conf не работает для сервисов».

---

### `Restart=always` без `StartLimitBurst`

```ini
# ❌ — сервис падает в бесконечном цикле, спамит логи, грузит систему
[Service]
Restart=always

# ✅
[Service]
Restart=on-failure
RestartSec=10s
StartLimitIntervalSec=120   # окно — 2 минуты
StartLimitBurst=5           # не более 5 перезапусков за 2 минуты
                            # после — failed, нужно ручное вмешательство
```

---

### Редактировать системный unit напрямую

```bash
# ❌ — изменения перезапишутся при обновлении пакета
nano /lib/systemd/system/nginx.service

# ✅ — override сохранится при обновлении пакета
systemctl edit nginx
# создаёт /etc/systemd/system/nginx.service.d/override.conf
```

