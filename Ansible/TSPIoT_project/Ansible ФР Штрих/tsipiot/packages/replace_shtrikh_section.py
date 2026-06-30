#!/usr/bin/env python3
"""
Заменяет секцию [Hw.fiscalregister.ShtrihM.N] на [Hw.fiscalregister.PosCenter.N]
в hw.ini, если у секции ShtrihM transport = tcp.
Идемпотентен: если секции ShtrihM с transport=tcp нет (например, уже заменена
или уже tcp нет) — ничего не делает и сообщает об этом.
"""
import re
import sys

HW_INI_PATH = "/linuxcash/cash/conf/drivers/hw.ini"

def main():
    with open(HW_INI_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    # Ищем секцию [Hw.fiscalregister.ShtrihM.N] до следующей секции [...] или конца файла
    section_pattern = re.compile(
        r"(\[Hw\.fiscalregister\.ShtrihM\.(\d+)\]\n)(.*?)(?=\n\[|\Z)",
        re.DOTALL
    )

    match = section_pattern.search(content)
    if not match:
        print("NOCHANGE: секция ShtrihM не найдена")
        sys.exit(0)

    section_num = match.group(2)
    section_body = match.group(3)

    # Проверяем transport = tcp внутри секции
    transport_match = re.search(r"^\s*transport\s*=\s*tcp\s*$", section_body, re.MULTILINE)
    if not transport_match:
        print("NOCHANGE: transport != tcp, замена не требуется")
        sys.exit(0)

    # Извлекаем нужные общие параметры
    def extract(param):
        m = re.search(rf"^\s*{param}\s*=\s*(.*?)\s*(#.*)?$", section_body, re.MULTILINE)
        return m.group(1).strip() if m else ""

    speed_enumerate = extract("speedEnumerate")
    host = extract("host")
    port = extract("port")
    connecttimeout = extract("connecttimeout")

    new_section = (
        f"[Hw.fiscalregister.PosCenter.{section_num}]\n"
        f"speedEnumerate = {speed_enumerate}    # Перебор скорости\n"
        f"transport = tcp\n"
        f"host = {host}   # Хост\n"
        f"port = {port}              # Порт\n"
        f"connecttimeout = {connecttimeout}      # Таймаут\n"
        f"baudrate = 115200        # Скорость\n"
        f"protocolType = v1        # Версия протокола\n"
    )

    full_match = match.group(0)
    # Сохраняем завершающий перевод строки, если он был у оригинальной секции
    trailing_newline = "\n" if full_match.endswith("\n") else ""
    new_content = content.replace(full_match, new_section.rstrip("\n") + trailing_newline)

    with open(HW_INI_PATH, "w", encoding="utf-8") as f:
        f.write(new_content)

    print(f"CHANGED: секция ShtrihM.{section_num} заменена на PosCenter.{section_num}")
    sys.exit(0)

if __name__ == "__main__":
    main()
