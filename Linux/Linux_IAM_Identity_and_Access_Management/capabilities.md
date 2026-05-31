## Capabilities — гранулярные привилегии процесса

### 1. Зачем нужны

Исторически в Linux была бинарная модель: либо обычный пользователь, либо полный root (UID 0). SUID — костыль из этой эпохи: он давал процессу **все** права root, хотя нужна была одна конкретная привилегия.

Capabilities разбивают неделимую власть root на ~40 независимых единиц. Процесс получает только то что ему нужно — и ничего лишнего:

```
[SUID]         → /bin/ping → EUID=0 → доступ ко ВСЕМ системным вызовам ❌
[Capabilities] → /bin/ping → только CAP_NET_RAW → только сырые сокеты  ✅
```

```bash
# старые дистрибутивы — SUID root
-rwsr-xr-x 1 root root /bin/ping

# современные — обычный файл с capabilities в атрибутах
-rwxr-xr-x 1 root root /bin/ping
getcap /bin/ping
# /bin/ping cap_net_raw=ep
```

> 💡 Обычным процессам capabilities не нужны совсем. Доступ к памяти, диску и устройствам контролируется обычными правами файлов (`rwx`). Capabilities нужны только для системных задач — сырой сокет, порт ниже 1024, монтирование ФС.

---

### 2. Пять наборов

> ⚠️ **Важно понять сразу:** пять наборов делятся на два типа управления:
>
> ```
> Через setcap (файловые атрибуты):   Permitted (p), Inheritable (i), Effective (e)
> Через capsh (атрибуты процесса):    Bounding, Ambient
> ```
>
> `setcap` записывает capabilities в файл на диске — один раз при настройке. `capsh` запускает процесс сразу с нужными capabilities — выставляет их и тут же стартует указанную программу. Bounding и Ambient — это про процесс, не про файл, поэтому `setcap` их не трогает вообще:
>
> ```bash
> setcap cap_net_raw=eip /bin/ping      # записать в файл на диске (e, i, p)
>
> capsh --drop=cap_sys_admin -- -c "exec /bin/bash"                            # Bounding процесса
> capsh --caps="cap_net_raw+eip" --addamb=cap_net_raw -- -c "/opt/deploy.py"  # Ambient процесса
> ```

У каждого процесса пять наборов capabilities. Смысл каждого — контроль над тем как capabilities распространяются:

| Набор | Вопрос | Аналогия |
|---|---|---|
| Bounding | что вообще возможно? | забор вокруг склада |
| Permitted | что разрешено иметь? | склад — всё что можно взять |
| Effective | что активно сейчас? | руки — что держишь прямо сейчас |
| Inheritable | что передать детям через бинарники? | накладная — нужна подпись с обеих сторон |
| Ambient | что передать детям через скрипты? | товар кладут в машину без накладной |

> 💡 Нельзя взять в руки (Effective) то чего нет на складе (Permitted). Через забор (Bounding) не перепрыгнуть — если capability не пустили через забор, она не попадёт на склад. Inheritable требует подписи с обеих сторон. Ambient передаёт автоматически без подписи от получателя.

---

**1. Bounding (CapBnd) — "что вообще возможно на этой машине"**
- **Назначение:** устанавливает абсолютный потолок на capabilities которые процесс может получить за всё время жизни. Биты можно только удалять — убрал бит, процесс и все его дети не вернут его никогда.

- **Важный момент — Bounding ≠ реальные права:**

По умолчанию все процессы наследуют полный Bounding set от PID 1 (init/systemd) — даже обычный bash обычного пользователя. Но это не значит что у них есть эти привилегии. Bounding — это просто потолок того что теоретически возможно получить. Реально использовать можно только то что есть в Permitted:

```
Запуск nginx без CapabilityBoundingSet:

Bounding:  ВСЕ capabilities  ← унаследовал от systemd
Permitted: только CAP_NET_BIND_SERVICE  ← прописано в файловых атрибутах nginx
Effective: пусто (пока сам не активирует)
```

Bounding полный — но это ничего не даёт само по себе. Без capability в Permitted процесс ничего не сможет сделать. Цепочка строго односторонняя:

```
Bounding  → потолок того что теоретически возможно
    ↓
Permitted → то что реально разрешено иметь (не может выйти за рамки Bounding)
    ↓
Effective → то что активно прямо сейчас (не может выйти за рамки Permitted)
```

`CapabilityBoundingSet` в systemd нужен чтобы поставить жёсткий потолок — даже если вдруг capability окажется в Permitted (например через эксплойт), Bounding не пропустит её дальше.

- **Как настраивается — четыре способа:**

> ⚠️ **Важный нюанс синтаксиса:** systemd единственный где указываешь что **оставить** — остальное дропается само. Все остальные способы работают через явное **исключение**:
> ```
> capsh --drop=cap_sys_admin               → убрать конкретную capability
> docker run --cap-drop=CAP_SYS_ADMIN      → убрать конкретную capability
> prctl(PR_CAPBSET_DROP, CAP_SYS_ADMIN)    → убрать конкретную capability
> CapabilityBoundingSet=CAP_NET_BIND_SERVICE → оставить только это, всё остальное дропается
> ```

**1. systemd** — самый распространённый в продакшене:
```ini
# /etc/systemd/system/nginx.service
[Service]
CapabilityBoundingSet=CAP_NET_BIND_SERVICE  # оставить только это, остальное дропается
```

**2. capsh** — для теста в консоли (обратите внимание на синтаксис):
```bash
sudo capsh --drop=cap_sys_admin -- -c "exec /bin/bash"  # убрать конкретную capability
```

**3. Программно через `prctl()`** — внутри кода программы, если она сама хочет урезать себе Bounding:
```c
prctl(PR_CAPBSET_DROP, CAP_SYS_ADMIN);  // убрать конкретную capability
```

**4. Docker/Kubernetes** — под капотом тот же `prctl()`:
```bash
docker run --cap-drop=CAP_SYS_ADMIN nginx  # убрать конкретную capability
```

На практике чаще всего используют systemd или Docker/Kubernetes. `capsh` — только для отладки. `prctl()` — если пишешь системную программу которая сама управляет своими привилегиями.

- **Почему это безопасно:** воркер nginx взломан, хакер пытается получить `CAP_SYS_ADMIN` — ядро смотрит Bounding → её там нет → отказ. Даже если эксплойт вернёт UID 0, `CAP_SYS_ADMIN` в Bounding нет — получить её невозможно физически.

---

**2. Permitted (CapPrm) — "разрешено иметь, но ещё не активно"**
- **Назначение:** определяет максимальный набор capabilities которым может обладать процесс. Процесс может поднять capability из Permitted в Effective, но не может добавить в Permitted то чего там нет.
- **Как работает:** capability лежит в Permitted как "запасная" — она есть у процесса, но ядро её не проверяет пока программа сама её не активирует. Работает как верхний предел привилегий — процесс не может выйти за рамки предопределённого набора.
- **Пример:** возьмём nginx из схемы выше. В Permitted у него `CAP_NET_BIND_SERVICE` — capability лежит в кармане, Effective пустой. nginx capabilities-aware: при старте не активирует её намеренно. Когда доходит до биндинга порта 80 — сам вызывает `capset()` (низкоуровневый системный вызов ядра через который программа управляет своими capabilities — может переложить из Permitted в Effective, убрать обратно или дропнуть навсегда) и просит ядро переложить `CAP_NET_BIND_SERVICE` из Permitted в Effective. Занимает порт, снова вызывает `capset()` и убирает capability из Effective обратно. Привилегия активна буквально несколько миллисекунд.

---

**3. Effective (CapEff) — "активно прямо сейчас"**
- **Назначение:** представляет capabilities которые процесс реально использует в данный момент. Именно этот набор ядро проверяет для разрешения или запрета операций.
- **Как работает:** ядро не знает и не следит за тем что происходит внутри программы. Оно реагирует в два разных момента:

  — когда nginx вызывает `capset()` — ядро проверяет Permitted и Bounding, и если всё в порядке, перекладывает capability в Effective

  — когда nginx вызывает `bind(80)` — ядро смотрит только на Effective. Есть `CAP_NET_BIND_SERVICE`? Да → разрешить. Нет → отказать. Permitted и Bounding к этому моменту уже отработали на шаге `capset()`.
- **Флаг `e` при setcap:** если программа не capabilities-aware и не умеет сама вызывать `capset()` — ставят флаг `e`. Тогда capability автоматически активируется в Effective при запуске без участия программы. Менее безопасно — capability активна всё время работы процесса, а не только когда нужна.

```
setcap cap_net_raw=p /bin/ping    # только Permitted, без Effective

ping запустился → cap_net_raw есть в Permitted → но НЕ активна
ping сразу пытается открыть сокет → ядро смотрит Effective → пусто → ❌ Permission denied
(ping не capabilities-aware, внутри нет capset() — некому переложить в Effective)

setcap cap_net_raw=ep /bin/ping   # Permitted + Effective

ping запустился → ядро само положило cap_net_raw в Effective
ping открывает сырой сокет → ядро проверяет Effective → есть → ✅
```

```bash
getcap /bin/ping
/bin/ping cap_net_raw=ep
# e = Effective (активна сразу при запуске)
# p = Permitted (разрешено иметь)
```

---

**4. Inherited (CapInh) — "что передать дочернему процессу через бинарники"**
- **Назначение:** определяет capabilities которые передаются дочернему процессу через `execve()`.
- **Как работает:** работает как двойная проверка — capability передаётся только если она есть в Inheritable у родителя И в файловых атрибутах запускаемого бинарника. Случайно или злонамеренно передать capability произвольному бинарнику не получится.
- **Ограничение:** процесс не может получить capabilities которых не было у родителя.
- **Пример:** nginx запускает `tcpdump` для диагностики сети. `tcpdump` нужна `CAP_NET_RAW`. Но просто положить capability в Inheritable у nginx недостаточно — нужно ещё прописать её в файловых атрибутах самого `tcpdump` через `setcap`. Файловые атрибуты — это то что `getcap` показывает для файла на диске:

```bash
getcap /usr/sbin/tcpdump
# (пусто — ничего не прописано, capability не передастся)

setcap cap_net_raw=i /usr/sbin/tcpdump
# теперь tcpdump явно "согласился" принять capability
```

```
# nginx имеет CAP_NET_RAW в Inheritable
# tcpdump НЕ имеет CAP_NET_RAW в файловых атрибутах

nginx запускает tcpdump → capability НЕ передаётся → ❌

# добавили CAP_NET_RAW в файловые атрибуты tcpdump
setcap cap_net_raw=i /usr/sbin/tcpdump

nginx запускает tcpdump → оба разрешили → capability передаётся → ✅
```

Нельзя случайно "заразить" произвольный бинарник — он должен явно принять capability через свои файловые атрибуты.

---

**5. Ambient (CapAmb) — "что передать дочернему процессу через скрипты"**
- **Назначение:** позволяет передавать capabilities дочерним процессам которые не имеют файловых capabilities.
- **Проблема которую решает:** `.py` и `.sh` файлы — текстовые, на них нельзя повесить capabilities через `setcap`. Попробуй:

```bash
setcap cap_net_raw=i /opt/deploy.py
# Error: не исполняемый файл нужного формата
```

`setcap` работает только с бинарными исполняемыми файлами (файл полученный после компиляции исходного кода — Go, Rust, C/C++ и других языков — в машинный код который процессор может выполнять напрямую). У скриптов нет файловых атрибутов для capabilities — значит через Inheritable они никогда ничего не получат, некуда записать "согласие" файла.

Может возникнуть идея повесить `setcap` не на скрипт, а на сам интерпретатор:

```bash
setcap cap_net_raw=ep /usr/bin/python3  # BAD
```

Но это дыра в безопасности — тогда **любой** Python-скрипт в системе получит `CAP_NET_RAW`, не только твой. Поэтому `AmbientCapabilities` в systemd — единственный безопасный способ дать capabilities конкретному скрипту.

- **Как работает:** в отличие от Inheritable где нужна подпись с обеих сторон, Ambient просто передаёт capability дочернему процессу при запуске — без проверки файла, без `setcap` на скрипте. Положил в Ambient у nginx — `deploy.py` получил автоматически.

- **Как настраивается через systemd:**

```ini
# /etc/systemd/system/monitor.service
[Service]
User=monitor
AmbientCapabilities=CAP_NET_RAW
ExecStart=/opt/monitor.py
```

Дочерние процессы сервиса получают capability автоматически:

```
monitor.py (Ambient: CAP_NET_RAW)
  └── sniffer.py → получает автоматически ✅
  └── analyzer.sh → получает автоматически ✅
```

Для быстрого теста в консоли:

```bash
capsh --caps="cap_net_raw+eip" --addamb=cap_net_raw -- -c "/opt/deploy.py"
# сначала выставляем capability в Permitted+Effective+Inheritable для capsh
# потом кладём в Ambient — скрипт получает автоматически
```

- **Ограничение ядра — нельзя положить в Ambient то чего нет в Permitted и Inheritable:** это ограничение актуально при ручном управлении через `capsh`. systemd обходит его автоматически — при указании `AmbientCapabilities=CAP_NET_RAW` он под капотом делает три шага последовательно: добавляет capability в Permitted, затем в Inheritable, затем в Ambient. Это просто удобная обёртка над ручными шагами.

- **Важное свойство:** Ambient — единственный набор который **не очищается** при смене пользователя. Когда процесс переключается с root на обычного пользователя — ядро автоматически очищает Permitted и Effective. Ambient остаётся нетронутым:

```
процесс стартует от root → переключается на UID 101
ядро очищает: Permitted ❌  Effective ❌
ядро НЕ трогает: Ambient ✅  ← capability доходит до процесса
```

Именно поэтому в `monitor.service` мы используем `AmbientCapabilities`:

```ini
[Service]
User=monitor              # запускаем не от root
AmbientCapabilities=CAP_NET_RAW  # capability попадает в Ambient
```

```
systemd кладёт CAP_NET_RAW в Ambient →
monitor.py стартует от пользователя monitor →
ядро очищает Permitted ❌ и Effective ❌ →
Ambient остаётся ✅ →
monitor.py может слушать сетевые пакеты ✅
```

Если бы использовали `PermittedCapabilities` вместо `AmbientCapabilities` — capability не дошла бы до процесса, ядро очистило бы её при смене пользователя.

---

### 3. Команды

```bash
# посмотреть capabilities файла
getcap /usr/sbin/tcpdump

# выставить capability на файл
setcap cap_net_bind_service=ep /usr/local/bin/node

# убрать все capabilities с файла
setcap -r /usr/local/bin/node

# посмотреть capabilities процесса (в hex)
cat /proc/$$/status | grep Cap

# декодировать hex в человеческий вид
capsh --decode=0000003fffffffff

# запустить процесс с урезанными правами для теста
sudo capsh --drop=cap_net_raw -- -c "exec /bin/ping -c 1 8.8.8.8"
# Результат: ping: icmp open socket: Operation not permitted
```

---

### 4. Контейнеры и изоляция

В Docker/Kubernetes процесс внутри контейнера по умолчанию запускается от `root` (UID 0) — но этот root жёстко ограничен. Контейнеризация использует механизм Capabilities для реализации принципа наименьших привилегий: при старте Docker/CRI-O (Container Runtime Interface — движок который непосредственно запускает контейнеры в Kubernetes, альтернатива Docker) сбрасывают большинство опасных capabilities из Bounding set, оставляя около 14 базовых (например, `CAP_CHOWN`, `CAP_NET_RAW`).

❌ **Антипаттерн — полный доступ:**
```bash
docker run --privileged ...
```
Флаг `--privileged` делает три вещи одновременно: возвращает в Bounding set все существующие capabilities; отключает профили AppArmor/SELinux (системы обязательного контроля доступа которые работают поверх capabilities и задают политики что конкретный процесс может делать — последний рубеж защиты); прокидывает все устройства хоста (`/dev/*`) внутрь контейнера — контейнер получает прямой доступ к железу хоста: дискам, сетевым картам, памяти, и может читать и писать напрямую на диск хоста минуя файловую систему. Это фактически превращает root в контейнере в полноценного root на хост-системе.

⚠️ **Красный флаг на ревью — скрытый privileged:**
```yaml
securityContext:
  capabilities:
    add: ["CAP_SYS_ADMIN"]  # самая опасная capability
```
`CAP_SYS_ADMIN` часто называют "новым root". Имея её, злоумышленник может выполнить `mount` и примонтировать физический диск хоста (`/dev/vda1`) внутрь контейнера — получить доступ к хостовому `/etc/shadow` или `crontab` и полностью скомпрометировать ноду.

✅ **Production-ready:**

> `NET_BIND_SERVICE` — позволяет процессу занимать порты ниже 1024 (80, 443) без прав root. По умолчанию такие порты доступны только root.

```yaml
securityContext:
  allowPrivilegeEscalation: false  # блокирует получение новых caps через SUID-бинарники
  readOnlyRootFilesystem: true     # ФС контейнера только на чтение
  runAsNonRoot: true               # запуск от не-root пользователя (UID != 0)
  capabilities:
    drop:
      - ALL                        # срезаем весь Bounding set в ноль
    add:
      - NET_BIND_SERVICE           # разрешаем только бинд портов < 1024 (если необходимо)
```

> 💡 **Middle+ нюанс:** на первый взгляд связка `runAsNonRoot: true` и `add: [NET_BIND_SERVICE]` выглядит как противоречие — запускаем не от root, но хотим дать capability для порта 80. Проблема в том что когда процесс меняет пользователя через `setuid()` — переключается с root на не-root — ядро автоматически очищает Permitted и Effective. Capability есть в конфиге, но до процесса не доходит:
>
> ```
> Kubernetes добавил NET_BIND_SERVICE в Permitted →
> nginx стартует от UID 101 →
> ядро очищает Permitted ❌ и Effective ❌ →
> nginx пытается занять порт 80 → Permission denied ❌
> ```
>
> Ambient здесь активируется автоматически — мы явно его не прописываем. В Kubernetes (1.22+) container runtime (CRI-O/containerd) при виде `add:` сам решает как реализовать передачу capability не-root процессу и автоматически кладёт её в Ambient до старта процесса. До версии 1.22 такого не было — `add:` клало только в Permitted/Effective и не-root процессы теряли capability при смене пользователя:
>
> ```
> Kubernetes читает add: [NET_BIND_SERVICE]
> Container runtime кладёт NET_BIND_SERVICE в Ambient до запуска процесса →
> nginx стартует от UID 101 →
> ядро очищает Permitted ❌ и Effective ❌ →
> Ambient остаётся нетронутым ✅ →
> nginx занимает порт 80 ✅
> ```

---
