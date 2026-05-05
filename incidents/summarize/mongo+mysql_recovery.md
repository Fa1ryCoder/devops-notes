# Что было сделано

---

## MongoDB

MongoDB падала при каждом запуске — процесс завершался с сигналом `ABRT` менее чем через секунду. В логах обнаружено повреждение файла метаданных `WiredTiger.wt` — 28 фрагментов с несовпадающими контрольными суммами.

Запущена стандартная команда восстановления:

```bash
mongod --repair --dbpath /var/lib/mongodb
```

Не помогла — выполнялась от пользователя `root` и сломала права на файлы данных. MongoDB работает от пользователя `mongodb` и не могла читать собственные файлы (`errno 13: Permission denied`). Исправлены права — MongoDB запустилась.

```bash
chown -R mongodb:mongodb /var/lib/mongodb
systemctl start mongod
```

---

## MySQL

MySQL не запускалась — падала с ошибкой чтения страницы в системном табличном пространстве InnoDB. Применён аварийный режим `innodb_force_recovery = 1` — база поднялась в режиме только для чтения.

```bash
echo "innodb_force_recovery = 1" >> /etc/mysql/mysql.conf.d/mysqld.cnf
systemctl restart mysql
```

Все попытки убрать `force_recovery` и запустить MySQL нормально заканчивались повторным падением. Проведена диагностика через системный каталог InnoDB:

```sql
SELECT SPACE, NAME, STATE
FROM information_schema.INNODB_TABLESPACES
WHERE STATE != 'normal';
```

Результат показал что повреждены только undo tablespaces (`undo_001`, `undo_002`) — служебные файлы журналов откатов транзакций. В запросе не оказалось ни одной пользовательской базы или таблицы со статусом отличным от `normal` — это означало что файлы данных (`.ibd`) не затронуты и сами данные целы. Повреждённые файлы переименованы в `.bak`, `force_recovery` убран из конфига, MySQL перезапущена — при старте автоматически пересоздала undo файлы и запустилась в полном режиме.

```bash
systemctl stop mysql
mv /var/lib/mysql/undo_001 /var/lib/mysql/undo_001.bak
mv /var/lib/mysql/undo_002 /var/lib/mysql/undo_002.bak
sed -i '/innodb_force_recovery/d' /etc/mysql/mysql.conf.d/mysqld.cnf
systemctl start mysql
```

Проверка записи после восстановления:

```bash
mysql -u netroot -pnetroot -e "CREATE DATABASE IF NOT EXISTS test_write; DROP DATABASE test_write;" && echo "ЗАПИСЬ РАБОТАЕТ"
```

**Итог:** обе базы данных восстановлены без потери данных и без переинициализации.
