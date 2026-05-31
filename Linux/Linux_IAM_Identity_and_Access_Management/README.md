# Linux IAM — Identity and Access Management
> Карта темы · DevOps Middle+

Эта директория покрывает всё что касается управления идентификацией и доступом в Linux: кто такие пользователи, как система проверяет их личность, что им разрешено делать с файлами и как всё это масштабируется на тысячи серверов.

---

## Структура темы

```
iam/
├── README.md                   ← вы здесь
├── users_and_sudo.md           ✅
├── permissions.md              ✅
├── authentication.md           ✅
├── sssd_ldap_ad.md             ✅
├── capabilities.md             ✅
└── selinux_apparmor.md         ⬜ TODO
```

---

## Три слоя IAM

```
Identity → кто ты в системе        → users_sudo.md + sssd_ldap_ad.md
Access   → что ты можешь с файлами → permissions.md
AuthN    → докажи что ты это ты    → authentication.md
```

---

## Документы

### ✅ [users_and_sudo.md](./users_and_sudo.md)
**Пользователи, группы и привилегии**

Фундамент темы. Как Linux идентифицирует пользователей на уровне ядра.

Покрывает: UID/GID, RUID/EUID/SUID процессов, `/etc/passwd`, `/etc/shadow`, `/etc/group`, NSS, типы пользователей, `useradd`/`usermod`/`userdel`, sudo и sudoers, `su`, антипаттерны, дебаг.

Связи: SUID как бит на файле → `permissions.md §8` · PAM полностью → `authentication.md` · SSSD/LDAP/AD → `sssd_ldap_ad.md`

---

### ✅ [permissions.md](./permissions.md)
**Права доступа к файлам**

Что пользователь может делать с файлами и директориями.

Покрывает: типы файлов, `rwx` для файлов и директорий, числовое представление, `chmod`, `chown`, `umask`, SUID/SGID/Sticky bit, ACL (`setfacl`/`getfacl`).

Связи: SUID как механизм смены UID процесса → `users_and_sudo.md §1` · гранулярные привилегии процессов → `capabilities.md` · принудительный контроль доступа → `selinux_apparmor.md`

> ⬜ **TODO:** добавить раздел про Linux Capabilities между §8 (спецбиты) и §9 (ACL)

---

### ✅ [authentication.md](./authentication.md)
**Аутентификация: PAM, SSH, 2FA**

Как система проверяет что пользователь тот за кого себя выдаёт.

Покрывает: архитектура аутентификации, PAM (модули, флаги, стеки), SSH (ключи, `sshd_config`, `authorized_keys`), `/etc/securetty`, `pam_faillock`, `limits.conf`, 2FA через Google Authenticator.

Связи: централизованная аутентификация (Kerberos, LDAP bind) → `sssd_ldap_ad.md`

---

### ✅ [sssd_ldap_ad.md](./sssd_ldap_ad.md)
**Централизованная аутентификация: SSSD, LDAP, Active Directory**

Как управлять пользователями когда серверов тысячи.

Покрывает: зачем нужна централизация, LDAP (структура, DN, атрибуты, `ldapsearch`), Active Directory, Kerberos (TGT, Service Ticket), SSSD (архитектура, `sssd.conf`), `realm join`, FreeIPA, диагностика (`sssctl`, `sss_cache`).

---

### ⬜ [capabilities.md](./capabilities.md) — TODO
**Linux Capabilities: гранулярные привилегии процессов**

Современная альтернатива SUID для точечной выдачи привилегий.

Планируется: что такое capabilities и зачем они нужны, пять наборов (permitted/effective/inheritable/bounding/ambient), `getcap`/`setcap`/`capsh`, почему современный `ping` не использует SUID, capabilities в контейнерах (Docker дропает большинство по умолчанию).

---

### ⬜ [selinux_apparmor.md](./selinux_apparmor.md) — TODO
**SELinux / AppArmor: принудительный контроль доступа**

Самый частый источник «непонятных» Permission denied на боевых серверах.

Планируется: DAC vs MAC, SELinux режимы (enforcing/permissive/disabled), контексты (`ls -Z`, `ps -Z`), типичный сценарий «права верные но всё равно отказ», `audit2allow`, `restorecon`, AppArmor профили и режимы.

---

## Рекомендуемый порядок изучения

```
1. users_and_sudo.md    ← начинать здесь: фундамент
2. permissions.md       ← что пользователи могут делать с файлами
3. authentication.md    ← как система их проверяет
4. sssd_ldap_ad.md      ← как это масштабируется в компании
── после создания ──
5. capabilities.md      ← современный механизм привилегий
6. selinux_apparmor.md  ← обязательно перед любым RHEL/CentOS собесом
```


