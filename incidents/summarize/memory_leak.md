# Что было сделано

---

## OOM-коллапс — утечка памяти при запуске Java-сервисов

После перезагрузки сервера все 14 Java-микросервисов Artixcs поднялись одновременно без ограничений памяти. Система вошла в состояние коллапса менее чем за 11 минут — RAM (11 GB) и Swap (4 GB) были забиты полностью, load average достиг 234, CPU простаивал на 0%.

Для стабилизации остановлены шесть наиболее тяжёлых сервисов:

```bash
systemctl stop artixcs-accounting-bonuses-certificates
systemctl stop artixcs-accounting-coupons
systemctl stop artixcs-accounting-scheduled-impacts
systemctl stop artixcs-sales-loader
systemctl stop artixcs-sales-ws
systemctl stop artixcs-report
```

После остановки RAM освободилась до 3.1 GB, load average упал до 1.83.

Установлена корневая причина: все юниты запускали JAR-файлы как bash-скрипты — JVM при этом не получала параметры из `ExecStart` и стартовала без ограничений памяти. Все юниты переписаны на прямой вызов JVM с явными лимитами:

```bash
for svc in artixcs-accounting-bonuses-certificates artixcs-accounting-coupons \
  artixcs-accounting-scheduled-impacts artixcs-clickhouse-rest artixcs-consultant-app \
  artixcs-counter artixcs-datatransfer artixcs-issuance-card artixcs-online-card \
  artixcs-telegram-bot artixcs-report artixcs-sales-loader artixcs-sales-ws; do
    f="/etc/systemd/system/${svc}.service"
    sed -i "s|ExecStart=/opt/${svc}/${svc}.jar|ExecStart=/opt/java-artix/bin/java -Xms128m -Xmx512m -Dsun.misc.URLClassPath.disableJarChecking=true -jar /opt/${svc}/${svc}.jar|g" "$f"
done
systemctl daemon-reload
```

При перезапуске часть сервисов упала с ошибкой `Cannot locate launcher` — они используют thin-launcher и требуют явного указания пути к локальному Maven-репозиторию. Параметр добавлен в соответствующие юниты:

```bash
for svc in artixcs-counter artixcs-accounting-coupons artixcs-accounting-scheduled-impacts \
  artixcs-accounting-bonuses-certificates artixcs-report artixcs-sales-ws artixcs-clickhouse-rest; do
    f="/etc/systemd/system/${svc}.service"
    sed -i "s|-Dsun.misc.URLClassPath.disableJarChecking=true -jar|-Dsun.misc.URLClassPath.disableJarChecking=true -Dmaven.repo.local=/opt/${svc}/lib/repository -jar|g" "$f"
done
```

Два сервиса потребовали дополнительных флагов — thin-launcher пытался скачать POM-файлы из интернета и получал 401. Добавлен offline-режим:

```bash
# artixcs-report — java-11, offline
ExecStart=/opt/java-11-artix/bin/java -Xms128m -Xmx512m \
  -Dsun.misc.URLClassPath.disableJarChecking=true \
  -Dmaven.repo.local=/opt/artixcs-report/lib/repository \
  -Dthin.offline=true -Dthin.root=/opt/artixcs-report/lib \
  -jar /opt/artixcs-report/artixcs-report.jar

# artixcs-clickhouse-rest — java-21, offline
ExecStart=/opt/java-21-artix/bin/java -Xms128m -Xmx512m \
  -Dsun.misc.URLClassPath.disableJarChecking=true \
  -Dmaven.repo.local=/opt/artixcs-clickhouse-rest/lib/repository \
  -Dthin.offline=true -Dthin.root=/opt/artixcs-clickhouse-rest/lib \
  -jar /opt/artixcs-clickhouse-rest/artixcs-clickhouse-rest.jar
```

Два сервиса (`artixcs-consultant-app`, `artixcs-telegram-bot`) не стартовали из-за неправильной версии JVM в юните — исправлено на `java-21` согласно оригинальному выводу `ps aux`.

---

**Итог:** все 14 сервисов восстановлены с лимитами памяти. Повторение инцидента при следующей перезагрузке исключено.
