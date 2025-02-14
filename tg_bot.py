import telebot
from telebot import types
import threading
import time
import random
import sqlite3
import json
import os
from urllib.parse import quote

TOKEN = '7798781312:AAH7VBFz_pDIYC400B4RoLfR5GOkZLhlv3M'
bot = telebot.TeleBot(TOKEN)


# Инициализация базы данных
def init_db():
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY,
            activated INTEGER DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tokens (
            token TEXT PRIMARY KEY,
            created_by INTEGER,
            created_at TEXT,
            used_by INTEGER DEFAULT NULL,
            used_at TEXT DEFAULT NULL
        )
    """)
    conn.commit()
    conn.close()


# Добавление chat_id в БД
def add_chat_id(chat_id):
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (chat_id, activated) VALUES (?, 0)", (chat_id,))
    conn.commit()
    conn.close()


# Получение всех chat_id из БД
def get_all_chat_ids():
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("SELECT chat_id FROM users WHERE activated = 1")
    chat_ids = [row[0] for row in cursor.fetchall()]
    conn.close()
    return chat_ids


def read_json_data():
    with open('rsi_alerts.json', 'r') as file:
        # Читаем файл построчно, так как каждая строка - отдельный JSON объект
        json_data = [json.loads(line) for line in file]
    return json_data


def format_number(number):
    if isinstance(number, str):
        try:
            # Обрабатываем числа в экспоненциальной нотации
            number = float(number)
        except ValueError:
            return number

    if number == 0:
        return "0"

    # Для очень маленьких чисел (меньше 0.001)
    if abs(number) < 0.001:
        # Увеличиваем точность до 8 знаков для малых значений
        return f"{number:.10f}".rstrip('0').rstrip('.') if '.' in f"{number:.10f}" else f"{number:.10f}"

    # Для чисел больше 1000
    if abs(number) >= 1000:
        # Проверяем, является ли число целым
        if number.is_integer():
            return f"{int(number):,}".replace(',', ' ')
        return f"{number:,.3f}".replace(',', ' ').rstrip('0').rstrip('.')

    # Для остальных чисел (с тремя знаками после запятой)
    return f"{number:.3f}".rstrip('0').rstrip('.')


def create_message(data):
    return (
        f"<b><i>Name:</i></b> {data['symbol']}\n"
        f"<b><i>RSI ({data['timeframe']}):</i></b> {format_number(data['rsi_value'])}\n"
        f"<b><i>Price:</i></b> {format_number(data['last_price'])} ({format_number(data['percent_price'])}%)\n"
        f"<b><i>Leverage:</i></b> {format_number(data['max_leverage'])}x\n"
        f"<b><i>Volume24:</i></b> {format_number(data['volume_24h'])}\n"
        f"\n"
        f"<b><i>Unspecified parameters</i></b>\n"
        f"<b><i>MaxPosSize:</i></b> {format_number(data['max_vol'])}\n"
        f"<b><i>MaxValPosUSDT:</i></b> {format_number(data['max_vol_price'])}"
    )


def create_keyboard(data):
    symbol = data['symbol']
    # Убираем "_USDT" и больше не обрезаем символы
    clean_symbol = symbol.replace("_USDT", "") if "_USDT" in symbol else symbol
    # Формируем ссылку для MEXC Futures
    mex_symbol = symbol.replace("_USDT", "") if "_USDT" in symbol else symbol
    mex_url = f"https://futures.mexc.co/ru-RU/exchange/{symbol}?type=linear_swap"


    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(
        types.InlineKeyboardButton(f"${clean_symbol}", url=mex_url),
        types.InlineKeyboardButton("MEXC", url="https://promote.mexc.com/r/EZw5RMrW"),
        types.InlineKeyboardButton("TradingView",
                                   url=f"https://ru.tradingview.com/chart/?symbol=MEXC%3A{mex_symbol}USDT.P")
    )
    return keyboard


def read_message_history():
    try:
        with open('message_history.json', 'r') as file:
            content = file.read()
            if not content:  # Если файл пустой
                return []
            return json.loads(content)
    except (FileNotFoundError, json.JSONDecodeError):
        # Создаем файл с пустым списком, если он не существует
        with open('message_history.json', 'w') as file:
            json.dump([], file)
        return []


def save_to_message_history(data):
    history = read_message_history()

    # Создаем новую запись
    new_record = {
        "id": len(history) + 1,
        "symbol": data['symbol'],
        "rsi_value": data['rsi_value'],
        "last_price": data['last_price'],
        "max_leverage": data['max_leverage'],
        "volume_24h": data['volume_24h'],
        "timeframe": data['timeframe'],
        "max_vol": data['max_vol'],
        "max_vol_price": data['max_vol_price'],
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
    }

    history.append(new_record)

    with open('message_history.json', 'w') as file:
        json.dump(history, file, indent=2)


def should_send_message(data):
    # Новая проверка на RSI = 100
    if float(data['rsi_value']) == 100:
        print(f"Пропуск отправки для {data['symbol']} - RSI = 100")
        return False

    history = read_message_history()

    # Проверяем историю сообщений
    for record in history:
        if (record['symbol'] == data['symbol'] and
                record['timeframe'] == data['timeframe']):
            # Проверяем разницу в RSI
            rsi_diff = abs(float(record['rsi_value']) - float(data['rsi_value']))
            # Отправляем сообщение только если разница больше или равна 5
            if rsi_diff < 5:
                return False
    return True


def send_periodic_messages():
    try:
        chat_ids = get_all_chat_ids()
        print(f"Получены chat_ids: {chat_ids}")

        json_data = read_json_data()
        print(f"Прочитаны данные из rsi_alerts.json: {len(json_data)} записей")

        # Для каждого набора данных проверяем возможность отправки всем пользователям
        for data in json_data:
            # Пропускаем символы с _USD и оставляем только _USDT
            if not data['symbol'].endswith('_USDT'):
                continue

            if should_send_message(data):
                successful_send = True  # Флаг успешной отправки всем пользователям

                # Проходим по всем пользователям
                for chat_id in chat_ids:
                    try:
                        image_path = f"image/{data['symbol']}.png"
                        print(f"Попытка отправить сообщение для {data['symbol']} в чат {chat_id}")

                        if not os.path.exists(image_path):
                            print(f"Файл изображения не найден: {image_path}")
                            successful_send = False
                            break

                        try:
                            # Проверяем, не заблокировал ли пользователь бота
                            bot.get_chat(chat_id)
                        except telebot.apihelper.ApiException as chat_error:
                            print(f"Ошибка доступа к чату {chat_id}: {chat_error}")
                            successful_send = False
                            continue

                        with open(image_path, 'rb') as photo:
                            message = bot.send_photo(
                                chat_id=chat_id,
                                photo=photo,
                                caption=create_message(data),
                                reply_markup=create_keyboard(data),
                                parse_mode="HTML"
                            )
                            print(f"Сообщение успешно отправлено для {data['symbol']} в чат {chat_id}")

                    except telebot.apihelper.ApiException as e:
                        print(f"Ошибка API Telegram для chat_id {chat_id}: {str(e)}")
                        successful_send = False
                    except Exception as e:
                        print(f"Общая ошибка отправки для chat_id {chat_id}: {str(e)}")
                        successful_send = False

                # Записываем в историю только если сообщение было успешно отправлено всем пользователям
                if successful_send:
                    save_to_message_history(data)
                    print(f"Данные сохранены в историю для {data['symbol']}")
            else:
                print(f"Сообщение для {data['symbol']} уже было отправлено ранее")

    except Exception as e:
        print(f"Общая ошибка в send_periodic_messages: {str(e)}")

    # Запускаем следующую проверку через 30 секунд
    threading.Timer(10, send_periodic_messages).start()


def clear_message_history():
    try:
        with open('message_history.json', 'w') as file:
            json.dump([], file)
            print("История сообщений успешно очищена")
    except Exception as e:
        print(f"Ошибка при очистке истории сообщений: {str(e)}")
    # Повторяем каждые 4 часа (14400 секунд)
    threading.Timer(14400, clear_message_history).start()


def clear_rsi_alerts():
    try:
        with open('rsi_alerts.json', 'w') as file:
            file.write('')  # Записываем пустую строку вместо JSON-массива
            print("Файл rsi_alerts успешно очищен (включая удаление [])")
    except Exception as e:
        print(f"Ошибка при очистке rsi_alerts: {str(e)}")
    # Повторяем каждые 4 часа (14400 секунд)
    threading.Timer(14400, clear_rsi_alerts).start()


# Обновляем список администраторов
ADMIN_IDS = [5874305622, 333418387]  # Добавляем второго администратора

@bot.message_handler(commands=['start'])
def handle_start(message):
    chat_id = message.chat.id
    add_chat_id(chat_id)
    bot.send_message(chat_id, "🔑 Для доступа к боту введите полученный ключ активации:")


@bot.message_handler(commands=['key_word'])
def handle_key_word(message):
    if message.chat.id not in ADMIN_IDS:  # Обновляем проверку
        return

    token = generate_invite_token()
    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO tokens (token, created_by, created_at) VALUES (?, ?, ?)",
                   (token, message.chat.id, time.strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()

    bot.send_message(
        message.chat.id,
        "🆕 Новый ключ доступа (нажмите для копирования):\n\n" +
        f"`{token}`",
        parse_mode="Markdown"
    )


def generate_invite_token(length=16):
    chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
    return ''.join(random.choice(chars) for _ in range(length))


@bot.message_handler(commands=['all_users'])
def handle_all_users(message):
    if message.chat.id not in ADMIN_IDS:  # Обновляем проверку
        return

    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    cursor.execute("SELECT chat_id FROM users WHERE activated = 1")
    users = cursor.fetchall()
    conn.close()

    response = "📊 Список активированных пользователей:\n\n"
    for user in users:
        try:
            chat_info = bot.get_chat(user[0])
            user_info = (
                f"ID: `{chat_info.id}`\n"
                f"Имя: {chat_info.first_name or 'Не указано'}\n"
                f"Фамилия: {chat_info.last_name or 'Не указана'}\n"
                f"Никнейм: @{chat_info.username or 'Нет'}\n"
                "───────────────────\n"
            )
            response += user_info
        except telebot.apihelper.ApiException:
            response += f"❌ Пользователь {user[0]} заблокировал бота\n───────────────────\n"

    bot.send_message(
        message.chat.id, 
        response,
        parse_mode="Markdown"
    )


@bot.message_handler(commands=['delet_user'])
def handle_delete_user(message):
    if message.chat.id not in ADMIN_IDS:  # Обновляем проверку
        return

    try:
        target_id = int(message.text.split()[1])
    except (IndexError, ValueError):
        bot.send_message(message.chat.id, "❌ Неверный формат команды. Используйте: /delet_user <chat_id>")
        return

    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()

    # Удаляем пользователя
    cursor.execute("DELETE FROM users WHERE chat_id = ?", (target_id,))
    # Сбрасываем использованные токены
    cursor.execute("UPDATE tokens SET used_by = NULL WHERE used_by = ?", (target_id,))

    conn.commit()
    conn.close()

    bot.send_message(message.chat.id, f"✅ Пользователь {target_id} успешно удален")


@bot.message_handler(func=lambda message: True)
def handle_token(message):
    chat_id = message.chat.id
    token = message.text.strip()

    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()

    # Проверяем токен
    cursor.execute("SELECT * FROM tokens WHERE token = ? AND used_by IS NULL", (token,))
    valid_token = cursor.fetchone()

    if valid_token:
        # Активируем пользователя
        cursor.execute("UPDATE users SET activated = 1 WHERE chat_id = ?", (chat_id,))
        cursor.execute("UPDATE tokens SET used_by = ?, used_at = ? WHERE token = ?",
                       (chat_id, time.strftime("%Y-%m-%d %H:%M:%S"), token))
        conn.commit()
        bot.send_message(chat_id, "✅ Активация успешна! Теперь вы будете получать уведомления.")
    else:
        bot.send_message(chat_id, "❌ Неверный или уже использованный токен")

    conn.close()


if __name__ == '__main__':
    init_db()
    
    # Генерация начальных токенов для всех администраторов
    initial_token = generate_invite_token()
    print(f"\n🚨 Сгенерирован стартовый токен доступа: {initial_token}\n")

    conn = sqlite3.connect("bot_data.db")
    cursor = conn.cursor()
    # Исправляем ошибку с кортежем - вставляем для первого администратора
    cursor.execute("INSERT OR IGNORE INTO tokens (token, created_by, created_at) VALUES (?, ?, ?)",
                   (initial_token, ADMIN_IDS[0], time.strftime("%Y-%m-%d %H:%M:%S"))) 
    conn.commit()
    conn.close()

    print("Бот запущен...")
    send_periodic_messages()
    clear_message_history()
    clear_rsi_alerts()
    bot.polling(none_stop=True)