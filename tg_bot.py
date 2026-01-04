import time
import os
import shutil
import datetime
import requests
import psutil
import sys
from dotenv import load_dotenv # <--- Импортируем библиотеку

# ===========================
# ⚙️ НАСТРОЙКИ
# ===========================

load_dotenv()


TG_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


if not TG_BOT_TOKEN or not TG_CHAT_ID:
    print("❌ CRITICAL ERROR: Токены не найдены! Проверьте файл .env")
    sys.exit(1)


MAIN_BOT_FILE = "DS_PM_eng.py" 

DB_FILE = "prediction_market.db"
HEARTBEAT_FILE = "heartbeat.txt"
BACKUP_FOLDER = "backups"

# Таймаут (секунды)
TIMEOUT_SECONDS = 180 
# ===========================

if not os.path.exists(BACKUP_FOLDER):
    os.makedirs(BACKUP_FOLDER)

def send_telegram(text):
    """Отправка текста в ТГ"""
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TG_CHAT_ID, "text": text})
    except Exception as e:
        print(f"Ошибка отправки в TG: {e}")

def send_telegram_file(filename, caption=""):
    """Отправка файла (бэкапа) в ТГ"""
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendDocument"
    try:
        with open(filename, 'rb') as f:
            requests.post(url, data={"chat_id": TG_CHAT_ID, "caption": caption}, files={"document": f})
    except Exception as e:
        print(f"Ошибка отправки файла в TG: {e}")

def is_process_running():
    """Проверка, запущен ли вообще python скрипт"""
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            # Проверяем, есть ли процесс python, в аргументах которого есть имя нашего файла
            if proc.info['name'] and 'python' in proc.info['name'].lower():
                if proc.info['cmdline'] and any(MAIN_BOT_FILE in arg for arg in proc.info['cmdline']):
                    return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return False

def check_heartbeat():
    """Проверка, не завис ли бот (проверка файла пульса)"""
    if not os.path.exists(HEARTBEAT_FILE):
        return True # Файла пока нет, даем шанс запуститься
    
    try:
        with open(HEARTBEAT_FILE, 'r') as f:
            last_beat = float(f.read().strip())
        
        # Если пульс был обновлен более TIMEOUT_SECONDS назад
        if time.time() - last_beat > TIMEOUT_SECONDS:
            return False 
    except:
        return True # Ошибка чтения (например, файл занят), игнорируем
        
    return True

# --- ГЛАВНЫЙ ЦИКЛ ---
print(f"🛡️ Сторож запущен! Слежу за: {MAIN_BOT_FILE}")
send_telegram(f"🛡️ Сторож запущен на сервере! Слежу за `{MAIN_BOT_FILE}`")

last_backup_time = time.time()
alert_sent = False 

while True:
    # 1. ПРОВЕРКИ
    process_alive = is_process_running()
    pulse_alive = check_heartbeat()
    
    if not process_alive:
        if not alert_sent:
            send_telegram(f"🚨 **ALARM!** Процесс `{MAIN_BOT_FILE}` упал (не найден в задачах)!")
            alert_sent = True
    elif not pulse_alive:
        if not alert_sent:
            send_telegram(f"⚠️ **WARNING!** Бот `{MAIN_BOT_FILE}` завис! (Нет пульса > {TIMEOUT_SECONDS}с)")
            alert_sent = True
    else:
        # Если бот поднялся после падения
        if alert_sent:
            send_telegram(f"✅ Бот `{MAIN_BOT_FILE}` снова в строю!")
            alert_sent = False

    # 2. БЭКАП (Раз в 24 часа)
    if time.time() - last_backup_time > 86400: # 86400 сек = 24 часа
        ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
        backup_path = f"{BACKUP_FOLDER}/backup_{ts}.db"
        try:
            if os.path.exists(DB_FILE):
                shutil.copy2(DB_FILE, backup_path)
                send_telegram_file(backup_path, caption=f"📦 Ежедневный бэкап: {ts}")
                last_backup_time = time.time()
                
                # Чистка старых (оставляем 3 последних)
                files = sorted([os.path.join(BACKUP_FOLDER, f) for f in os.listdir(BACKUP_FOLDER)])
                while len(files) > 3:
                    os.remove(files[0])
                    files.pop(0)
            else:
                print("⚠️ База данных не найдена для бэкапа (это нормально при первом запуске)")
        except Exception as e:
            send_telegram(f"❌ Не удалось сделать бэкап: {e}")

    time.sleep(10) # Проверка каждые 10 секунд