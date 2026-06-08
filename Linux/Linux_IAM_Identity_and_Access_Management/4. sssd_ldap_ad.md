# Централизованная аутентификация: SSSD, LDAP, Active Directory
> Углублённая лекция · DevOps Middle+

---

## Содержание

1. [Зачем нужна централизованная аутентификация](#1-зачем-нужна-централизованная-аутентификация)
2. [LDAP — протокол и структура каталога](#2-ldap--протокол-и-структура-каталога)
3. [Active Directory = LDAP + Kerberos + DNS](#3-active-directory)
4. [Kerberos — билетная система аутентификации](#4-kerberos)
5. [SSSD — архитектура и конфигурация](#5-sssd--архитектура-и-конфигурация)
6. [Интеграция Linux с Active Directory](#6-интеграция-linux-с-active-directory)
7. [FreeIPA — open source альтернатива AD](#7-freeipa)
8. [Диагностика и траблшутинг](#8-диагностика-и-траблшутинг)
9. [Анти-паттерны](#9-анти-паттерны)
10. [Реальные кейсы с дебагом](#10-реальные-кейсы-с-дебагом)
11. [Вопросы на собесе](#11-вопросы-на-собесе)
12. [Шпаргалка](#12-шпаргалка)

---

## 1. Зачем нужна централизованная аутентификация

### Проблема: 1000 серверов, 200 пользователей

В небольшой компании с 5 серверами можно управлять пользователями вручную через `useradd`. В компании с сотнями серверов это невозможно:

```
Проблемы без централизации:
─────────────────────────────────────────────────────────
Инженер уволился         → нужно удалить аккаунт на каждом сервере
Новый сотрудник          → создать аккаунт на каждом сервере вручную
Смена пароля             → обновить на каждом сервере
Аудит: кто куда ходил    → логи разбросаны по всем серверам
Единая политика паролей  → сложно обеспечить при локальных аккаунтах
```

### Решение: централизованное хранилище учётных записей

```
Администратор
      │  создаёт пользователя ОДИН РАЗ
      ▼
 Active Directory / LDAP / FreeIPA
      │
      ├── server-01  ←── SSSD ──→ AD
      ├── server-02  ←── SSSD ──→ AD
      ├── server-03  ←── SSSD ──→ AD   Все серверы обращаются
      └── server-N   ←── SSSD ──→ AD   к одному источнику

Уволился сотрудник → отключить в AD → мгновенно потерял доступ везде
```

### Три задачи которые решает SSSD

**Identity** (кто существует): `getent passwd alice@company.com` должно работать
**Authentication** (проверка пароля/токена): PAM через SSSD
**Authorization** (что можно): `/etc/sudoers` + SSSD + группы AD

---

## 2. LDAP — протокол и структура каталога

### Что такое LDAP

**LDAP (Lightweight Directory Access Protocol)** — протокол доступа к иерархическому каталогу объектов. Каталог оптимизирован для чтения: данные меняются редко, читаются очень часто.

Порты: `389` (LDAP), `636` (LDAPS — LDAP over TLS), `3268/3269` (Global Catalog в AD).

### Структура каталога (DIT — Directory Information Tree)

```
dc=company,dc=com               ← корень (domain component)
├── ou=users                    ← organizational unit
│   ├── cn=alice                ← объект пользователя
│   ├── cn=bob
│   └── cn=charlie
├── ou=groups
│   ├── cn=developers
│   └── cn=admins
└── ou=serviceaccounts
    ├── cn=jenkins
    └── cn=gitlab-runner
```

### DN — Distinguished Name

Полный путь к объекту в дереве, читается справа налево:

```
cn=alice,ou=users,dc=company,dc=com
   ─────  ────────  ────────  ────
    ↑        ↑         ↑        ↑
  имя    отдел    домен.com   домен
```

### Атрибуты объекта пользователя

```bash
# Посмотреть атрибуты пользователя через ldapsearch
ldapsearch -x -H ldap://dc.company.com \
  -D "cn=admin,dc=company,dc=com" -W \
  -b "ou=users,dc=company,dc=com" \
  "(uid=alice)"
```

Стандартные LDAP-атрибуты для Linux-пользователей (POSIX schema):

```
dn: cn=alice,ou=users,dc=company,dc=com
objectClass: posixAccount
objectClass: shadowAccount
uid: alice                    ← Unix username
cn: Alice Smith               ← Common Name
uidNumber: 10001              ← Unix UID
gidNumber: 10000              ← Primary GID
homeDirectory: /home/alice
loginShell: /bin/bash
userPassword: {SSHA}...       ← хэш пароля
shadowLastChange: 19742
shadowMax: 90                 ← максимальный срок пароля в днях
```

### LDAP операции

```bash
# Поиск (самое частое)
ldapsearch -x -H ldap://ldap.company.com \
  -b "dc=company,dc=com" \
  "(uid=alice)"              # фильтр

# Анонимный поиск (если разрешён)
ldapsearch -x -H ldap://ldap.company.com \
  -b "dc=company,dc=com" "(objectClass=posixAccount)" uid uidNumber

# Аутентифицированный bind
ldapsearch -x -H ldap://ldap.company.com \
  -D "cn=readonly,dc=company,dc=com" -W \
  -b "dc=company,dc=com" "(uid=alice)"

# Проверить подключение и TLS
ldapsearch -x -H ldaps://ldap.company.com \
  -b "" -s base "(objectClass=*)" namingContexts
```

---

## 3. Active Directory

### AD = LDAP + Kerberos + DNS + Group Policy

Active Directory — это Microsoft-реализация LDAP с существенными расширениями:

| Компонент | Что даёт |
|-----------|---------|
| LDAP (порт 389/636) | Хранение объектов: пользователи, группы, компьютеры |
| Kerberos (порт 88) | Аутентификация без передачи пароля по сети |
| DNS (порт 53) | Поиск контроллеров домена, SRV-записи |
| Group Policy | Централизованное управление настройками |
| Kerberos PAC | Авторизационные данные в билете (группы, атрибуты) |

### Структура AD

```
Лес (Forest): company.com
└── Домен (Domain): company.com
    ├── Домен: eu.company.com
    └── Домен: us.company.com

Доверие (Trust): между доменами одного леса — автоматическое двустороннее
```

### Важные объекты AD для Linux-интеграции

```
Organizational Unit (OU): аналог папки, для структурирования
User: учётная запись пользователя
Computer: учётная запись компьютера (при join к домену)
Group: группа (Security Group — для прав, Distribution — для почты)
Service Principal Name (SPN): идентификатор сервиса для Kerberos
```

---

## 4. Kerberos

### Зачем нужен Kerberos

Основная проблема которую решает Kerberos: **пароль никогда не передаётся по сети** даже при первой аутентификации. Вместо этого используется система криптографических билетов.

### Участники

```
KDC (Key Distribution Center) = AS + TGS — центр доверия, обычно контроллер домена AD
  ├── AS (Authentication Service) — выдаёт TGT
  └── TGS (Ticket Granting Service) — выдаёт Service Tickets

Client — пользователь или сервис
Service — ресурс к которому нужен доступ (файловый сервер, веб-сервис)
```

### Процесс аутентификации

```
── Шаг 1: получить TGT (Ticket Granting Ticket) ──────────────────

Клиент                    AS (часть KDC)
   │── "Я Alice, дайте TGT" ──────────────────────────────→│
   │                                               проверяет пароль локально
   │                                               (хэш используется как ключ)
   │←── TGT (зашифрован ключом KDC) ──────────────────────│
   │    TGT содержит: имя пользователя, срок действия,
   │    session key (для общения с TGS)

── Шаг 2: получить Service Ticket для конкретного сервиса ────────

Клиент                    TGS (часть KDC)
   │── TGT + "хочу доступ к server.company.com" ──────────→│
   │                                               проверяет TGT
   │←── Service Ticket (зашифрован ключом сервиса) ────────│

── Шаг 3: аутентификация у сервиса ──────────────────────────────

Клиент                    Сервис
   │── Service Ticket ────────────────────────────────────→│
   │                                               расшифровывает своим ключом
   │                                               проверяет валидность
   │←── доступ разрешён ─────────────────────────────────│
```

Пароль не передавался ни разу. TGT действует обычно 10 часов — после этого нужно заново `kinit`.

### Kerberos на практике

```bash
# Получить TGT (требует пароль один раз)
kinit alice@COMPANY.COM
# Password for alice@COMPANY.COM:

# Посмотреть текущие билеты
klist
# Credentials cache: API:...
# Principal: alice@COMPANY.COM
#
# Issued            Expires           Principal
# Jan 15 10:00:00   Jan 15 20:00:00   krbtgt/COMPANY.COM@COMPANY.COM
# Jan 15 10:01:00   Jan 15 20:00:00   host/server.company.com@COMPANY.COM

# Обновить TGT (если не истёк)
kinit -R

# Уничтожить все билеты (аналог logout из домена)
kdestroy

# Проверить конфиг Kerberos
cat /etc/krb5.conf
```

```ini
# /etc/krb5.conf — минимальный пример
[libdefaults]
    default_realm = COMPANY.COM
    dns_lookup_realm = true    # искать realm через DNS SRV-записи
    dns_lookup_kdc = true      # искать KDC через DNS

[realms]
    COMPANY.COM = {
        kdc = dc01.company.com
        admin_server = dc01.company.com
    }

[domain_realm]
    .company.com = COMPANY.COM
    company.com = COMPANY.COM
```

---

## 5. SSSD — архитектура и конфигурация

### Что такое SSSD

**SSSD (System Security Services Daemon)** — демон который:
- подключается к identity-провайдерам (AD, LDAP, FreeIPA, Kerberos)
- кэширует результаты (пользователи могут логиниться даже если AD недоступен)
- предоставляет NSS (Name Service Switch) и PAM интерфейсы для системы

```
Linux-сервер
├── NSS → sssd → identity провайдер (AD/LDAP)
│    getent passwd alice@company.com
│
├── PAM → sssd → auth провайдер (Kerberos/LDAP bind)
│    аутентификация при логине
│
└── /var/lib/sss/db/ → локальный кэш (работает offline)
```

### Архитектура SSSD

```
sssd (главный процесс)
├── sssd_be  — backend, подключается к AD/LDAP
├── sssd_nss — обрабатывает NSS-запросы (getent, id)
├── sssd_pam — обрабатывает PAM-запросы (аутентификация)
└── sssd_sudo — обрабатывает sudo-правила из LDAP/AD
```

### /etc/sssd/sssd.conf — структура

```ini
[sssd]
services = nss, pam, sudo    # какие сервисы предоставлять
domains = company.com        # список доменов

[domain/company.com]
# Тип провайдера
id_provider = ad             # откуда брать пользователей
auth_provider = ad           # как аутентифицировать
sudo_provider = ad           # sudo-правила из AD

# AD-специфичные настройки
ad_domain = company.com
krb5_realm = COMPANY.COM
ad_server = dc01.company.com, dc02.company.com  # список DC

# Формат имени пользователя
use_fully_qualified_names = False  # alice вместо alice@company.com
fallback_homedir = /home/%u        # шаблон домашней директории
default_shell = /bin/bash

# Кэш
cache_credentials = true           # работать offline
krb5_store_password_if_offline = true

# Ограничить каких пользователей AD видит эта машина
ad_gpo_access_control = enforcing  # применять Group Policy
# simple_allow_groups = linux-admins, linux-users  # белый список групп
```

```bash
# Права на конфиг — обязательно 600!
chmod 600 /etc/sssd/sssd.conf
chown root:root /etc/sssd/sssd.conf
```

### Настройка NSS для использования SSSD

```bash
cat /etc/nsswitch.conf
# passwd:   files sss     ← сначала /etc/passwd, потом SSSD
# group:    files sss
# shadow:   files sss
# sudoers:  files sss     ← sudo-правила из AD
```

---

## 6. Интеграция Linux с Active Directory

### Предварительные требования

```bash
# 1. DNS должен разрешать AD домен
nslookup company.com           # должен найти AD DC
nslookup _ldap._tcp.company.com type=SRV   # SRV-записи AD

# 2. Синхронизация времени (критично для Kerberos: расхождение > 5 минут = отказ)
timedatectl status
# Рекомендуется использовать AD DC как NTP источник:
# server dc01.company.com iburst → /etc/chrony.conf

# 3. Hostname должен быть FQDN
hostnamectl set-hostname server01.company.com
```

### Установка пакетов

```bash
# Ubuntu/Debian
apt install -y sssd sssd-ad sssd-tools realmd adcli \
              krb5-user packagekit samba-common-bin oddjob oddjob-mkhomedir

# RHEL/CentOS
dnf install -y sssd realmd adcli krb5-workstation \
              oddjob oddjob-mkhomedir samba-common-tools
```

### Присоединение к домену через realm

```bash
# Обнаружить домен
realm discover company.com
# company.com
#   type: kerberos
#   realm-name: COMPANY.COM
#   domain-name: company.com
#   configured: no
#   server-software: active-directory
#   client-software: sssd

# Присоединиться (нужен аккаунт AD с правом join)
realm join -U Administrator company.com
# Password for Administrator:

# Проверить что join прошёл успешно
realm list
# company.com
#   type: kerberos
#   realm-name: COMPANY.COM
#   configured: kerberos-member
#   server-software: active-directory
#   client-software: sssd
#   required-package: sssd-tools
#   login-formats: %U@company.com
#   login-policy: allow-realm-logins
```

После `realm join` автоматически:
- создаётся `/etc/sssd/sssd.conf`
- обновляется `/etc/nsswitch.conf`
- настраивается `/etc/krb5.conf`
- создаётся учётная запись компьютера в AD

### Настройка mkhomedir — автосоздание домашних директорий

```bash
# Включить автосоздание /home при первом логине
pam-auth-update --enable mkhomedir   # Ubuntu
authselect select sssd with-mkhomedir --force  # RHEL

# Проверить что pam_mkhomedir.so есть в PAM
grep mkhomedir /etc/pam.d/common-session
# session optional pam_mkhomedir.so skel=/etc/skel/ umask=0022
```

### Тестирование интеграции

```bash
# Разрешить доступ всем пользователям домена (для тестирования)
realm permit --all
# Или только конкретным группам:
realm permit -g "linux-admins@company.com"

# Проверить что пользователь виден
id alice@company.com
# uid=1234567(alice) gid=1234567(alice) groups=1234567(alice),1234568(domain users)...

# Без FQDN если use_fully_qualified_names = False
id alice

# Проверить через getent
getent passwd alice@company.com
# alice@company.com:*:1234567:1234567:Alice Smith:/home/alice:/bin/bash

# Проверить группы
getent group "linux-admins@company.com"

# Проверить что аутентификация работает
su - alice@company.com
```

### sudo для AD-пользователей и групп

```bash
# В /etc/sudoers или /etc/sudoers.d/ad-users:

# Разрешить AD-группу (% + название + @домен)
%linux-admins@company.com  ALL=(ALL:ALL) ALL

# Без FQDN если use_fully_qualified_names = False
%linux-admins  ALL=(ALL:ALL) NOPASSWD: /usr/bin/systemctl
```

---

## 7. FreeIPA

### Что такое FreeIPA

**FreeIPA** — open source альтернатива Active Directory для Linux-окружений. Объединяет в одном решении:

```
FreeIPA
├── 389 Directory Server (LDAP) — хранение объектов
├── MIT Kerberos                 — аутентификация
├── Dogtag PKI / CA              — выдача сертификатов
├── BIND DNS                     — встроенный DNS сервер
├── NTP (chrony)                 — синхронизация времени
└── Web UI + CLI                 — удобное управление
```

### Основные команды IPA

```bash
# Войти (получить Kerberos TGT для admin)
kinit admin

# Пользователи
ipa user-add alice --first=Alice --last=Smith --email=alice@company.com
ipa user-find alice
ipa user-mod alice --shell=/bin/zsh
ipa user-disable alice   # заблокировать
ipa user-del alice

# Группы
ipa group-add linux-admins --desc="Linux Administrators"
ipa group-add-member linux-admins --users=alice,bob

# Sudo-правила
ipa sudorule-add allow-systemctl
ipa sudorule-add-allow-command allow-systemctl --sudocmds='/usr/bin/systemctl'
ipa sudorule-add-user allow-systemctl --groups=linux-admins
ipa sudorule-add-host allow-systemctl --hostgroups=production-servers

# Хосты
ipa host-add server01.company.com
ipa hostgroup-add production-servers
ipa hostgroup-add-member production-servers --hosts=server01.company.com

# Политики паролей
ipa pwpolicy-show
ipa pwpolicy-mod --minlength=12 --history=10 --maxlife=90
```

### Присоединение клиента к IPA

```bash
# Установка
apt install freeipa-client    # Ubuntu
dnf install ipa-client        # RHEL

# Join
ipa-client-install \
  --domain=company.com \
  --server=ipa.company.com \
  --realm=COMPANY.COM \
  --principal=admin \
  --mkhomedir \
  --unattended
```

### FreeIPA vs Active Directory

| | FreeIPA | Active Directory |
|--|---------|-----------------|
| Платформа | Linux-native | Windows-native |
| Лицензия | Open Source | Коммерческая |
| LDAP | 389 DS (Red Hat) | Microsoft AD LDAP |
| Kerberos | MIT Kerberos | Microsoft Kerberos |
| Интеграция с Linux | Нативная | Через SSSD/realm |
| Group Policy | Нет (HBAC + sudo rules) | Есть |
| Trusts с AD | Возможны | Нативно |
| Web UI | Да | Нет (ADUC — Windows) |

---

## 8. Диагностика и траблшутинг

### Команды диагностики SSSD

```bash
# Проверить статус SSSD
systemctl status sssd

# Посмотреть логи SSSD (очень подробные)
tail -f /var/log/sssd/sssd_company.com.log
tail -f /var/log/sssd/sssd_pam.log
tail -f /var/log/sssd/sssd_nss.log

# Включить debug-логирование (в /etc/sssd/sssd.conf)
[domain/company.com]
debug_level = 7   # 0 = только ошибки, 9 = максимум

# Перезапустить после изменения
systemctl restart sssd

# Полная диагностика пользователя
sssctl user-checks alice
# user: alice
# account_info_err: Аккаунт найден в кэше и у провайдера
# ...

# Информация о кэше
sssctl domain-status company.com
# Online status: Online   ← важно! если Offline — проблема с подключением к AD

# Очистить кэш (если данные устарели)
sss_cache -G        # очистить кэш групп
sss_cache -U        # очистить кэш пользователей
sss_cache -E        # полная очистка

# Или через sssctl (новее)
sssctl cache-remove --override  # удалить весь локальный кэш
```

### Проверка подключения к AD/LDAP

```bash
# Проверить Kerberos
kinit alice@COMPANY.COM && klist
# Если ошибка "Cannot contact any KDC" — проблема с DNS или firewall

# Проверить LDAP-подключение напрямую
ldapsearch -x -H ldap://dc01.company.com \
  -D "alice@company.com" -W \
  -b "dc=company,dc=com" \
  "(sAMAccountName=alice)"

# DNS диагностика AD
nslookup -type=SRV _ldap._tcp.company.com
nslookup -type=SRV _kerberos._tcp.company.com
nslookup -type=SRV _kerberos._udp.company.com

# Проверить порты
nc -zv dc01.company.com 389   # LDAP
nc -zv dc01.company.com 636   # LDAPS
nc -zv dc01.company.com 88    # Kerberos
nc -zv dc01.company.com 464   # kpasswd
```

### adcli и realm диагностика

```bash
# Проверить статус присоединения к домену
realm list

# Проверить учётную запись компьютера в AD
adcli info company.com

# Обновить учётную запись компьютера (если истёк пароль)
adcli update-computer --verbose

# Покинуть домен
realm leave company.com
```

---

## 9. Анти-паттерны

**Использование Domain Admin для join:**
Лучше создать отдельный аккаунт с минимальными правами только для присоединения компьютеров к домену. Domain Admin в конфиге sssd — огромный риск.

**Отключение кэширования (cache_credentials = false):**
При недоступности AD пользователи не смогут войти. В production всегда оставляйте кэш включённым.

**use_fully_qualified_names = True в конфигах без обдумывания:**
Если включено — имена везде будут `alice@company.com`. Скрипты, crontab, файлы конфигурации должны это учитывать. Решите сразу и делайте везде консистентно.

**Нет мониторинга статуса SSSD:**
SSSD может уйти в Offline и никто не заметит. Добавьте в мониторинг: `sssctl domain-status company.com` должен отвечать "Online".

**Игнорирование синхронизации времени:**
Kerberos требует расхождение не более 5 минут. Расхождение времени — самая частая причина «непонятных» отказов аутентификации. Всегда настраивайте NTP с DC как источником.

**Realm permit --all в production:**
Разрешает вход ЛЮБОМУ пользователю AD. Используйте `realm permit -g specific-group`. Принцип минимальных привилегий.

---

## 10. Реальные кейсы с дебагом

### Кейс 1: пользователь не может войти, был раньше

```bash
# Симптом: alice могла войти вчера, сегодня — нет

# Шаг 1: проверить статус SSSD
sssctl domain-status company.com
# Online status: Offline  ← вот причина!

# AD недоступен, кэш есть, но cache_credentials = false в конфиге
# Или кэш устарел

# Шаг 2: проверить сетевой доступ к DC
ping dc01.company.com
nc -zv dc01.company.com 389

# Шаг 3: если AD доступен но SSSD не видит его
systemctl restart sssd
# Иногда SSSD "зависает" в Offline и не замечает что AD снова доступен
```

### Кейс 2: id alice работает, sudo не работает

```bash
# Симптом: id alice — OK, но sudo не даёт права хотя группа есть

# Шаг 1: проверить что sudoers видит AD группу
sudo -l -U alice
# User alice may not run sudo on server  ← группа не матчится

# Шаг 2: проверить формат имени в /etc/sudoers
# Если use_fully_qualified_names = True:
# %linux-admins@company.com  ALL=(ALL) ALL  ← нужен FQDN
# Если False:
# %linux-admins  ALL=(ALL) ALL

# Шаг 3: проверить членство в группе как видит SSSD
getent group linux-admins
# linux-admins:*:12345:alice,bob  ← alice должна быть здесь

# Если группы нет — очистить кэш
sss_cache -G
getent group linux-admins
```

### Кейс 3: медленный вход (30-60 секунд)

```bash
# Симптом: вход занимает очень долго

# Причина 1: SSSD ждёт таймаута попытки второго DC
# Проверить:
dig SRV _ldap._tcp.company.com
# Если в ответе много DC — SSSD пробует каждый по очереди

# Решение: явно указать DC в sssd.conf
[domain/company.com]
ad_server = dc01.company.com   # основной
ad_backup_server = dc02.company.com

# Причина 2: reverse DNS lookup зависает
# В sssd.conf:
[domain/company.com]
lookup_family_order = ipv4_only   # не ждать IPv6 reverse lookup
```

---

## 11. Вопросы на собесе

**«Почему нельзя просто добавить пользователей вручную на всех серверах?»**
Управление учётными записями не масштабируется: увольнение сотрудника требует удаления с каждого сервера, единая политика паролей невозможна, нет единого аудита. Централизованное решение (AD/LDAP/FreeIPA) решает всё это в одной точке.

**«В чём разница между LDAP и Active Directory?»**
AD — реализация Microsoft поверх LDAP с добавлением Kerberos (для аутентификации без передачи пароля), DNS (SRV-записи для обнаружения DC), Group Policy и расширений схемы. Чистый LDAP — только протокол доступа к каталогу, без встроенной аутентификации Kerberos.

**«Зачем Kerberos если LDAP уже есть?»**
LDAP с простым bind-ом передаёт пароль (в зашифрованном виде) при каждой аутентификации. Kerberos вообще не передаёт пароль по сети — только при получении первого TGT, и то в зашифрованном виде. После этого — только билеты. Это значительно безопаснее, особенно в корпоративной сети.

**«Что такое SSSD и что будет если он упадёт?»**
SSSD — демон-посредник между системой и identity-провайдером. При падении SSSD: `getent passwd` не будет возвращать AD пользователей, аутентификация AD пользователей провалится. Но если `cache_credentials = true` — пользователи у которых есть кэш смогут войти по кэшированным данным. Локальные пользователи из `/etc/passwd` не затронуты.

**«Как проверить что Linux-сервер правильно интегрирован с AD?»**
```bash
realm list                               # статус домена
sssctl domain-status company.com         # Online/Offline
id alice@company.com                     # разрешается ли пользователь
kinit alice@COMPANY.COM && klist         # работает ли Kerberos
getent passwd alice@company.com          # NSS видит пользователя
ssh alice@company.com@localhost          # аутентификация работает
```

---

## 12. Шпаргалка

```bash
# realm — управление доменом
realm discover company.com              # обнаружить домен
realm join -U Administrator company.com # присоединиться
realm leave company.com                 # покинуть домен
realm list                              # статус
realm permit -g linux-admins            # разрешить группу

# SSSD
systemctl status sssd
sssctl domain-status company.com        # Online/Offline?
sssctl user-checks alice                # диагностика пользователя
sss_cache -G && sss_cache -U            # очистить кэш групп/пользователей
tail -f /var/log/sssd/sssd_*.log        # логи

# Проверка пользователей
id alice@company.com                    # UID, GID, группы
getent passwd alice@company.com         # через NSS
getent group linux-admins               # члены группы

# Kerberos
kinit alice@COMPANY.COM                 # получить TGT
klist                                   # показать билеты
kdestroy                                # уничтожить билеты
kinit -R                                # обновить TGT

# LDAP диагностика
ldapsearch -x -H ldap://dc.company.com \
  -D "alice@company.com" -W \
  -b "dc=company,dc=com" "(sAMAccountName=alice)"

# Сеть
nslookup -type=SRV _ldap._tcp.company.com
nc -zv dc01.company.com 88 389 636      # проверить порты

# FreeIPA
kinit admin
ipa user-find alice
ipa group-add-member linux-admins --users=alice
ipa-client-install --domain=company.com --mkhomedir
```
