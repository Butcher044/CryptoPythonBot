import requests
import time
import numpy as np
import aiohttp
import asyncio
from tqdm.asyncio import tqdm_asyncio
import json

DETECTION_FILE = "rsi_alerts.json"
HISTORY_FILE = "rsi_history.json"
RSI_THRESHOLD = 20
RSI_UPPER_THRESHOLD = 80

detected_assets = []


def calculate_rsi(prices, period=14):
    deltas = np.diff(prices)
    seed = deltas[:period]
    gains = np.maximum(seed, 0)
    losses = np.abs(np.minimum(seed, 0))

    avg_gain = np.mean(gains)
    avg_loss = np.mean(losses)

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    # Сглаживание для последующих значений
    for i in range(period, len(deltas)):
        delta = deltas[i]
        gain = max(delta, 0)
        loss = abs(min(delta, 0))

        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

        if avg_loss == 0:
            rs = float('inf')
        else:
            rs = avg_gain / avg_loss

        rsi = np.append(rsi, 100 - (100 / (1 + rs)))

    return rsi[-1]  # Возвращаем последнее значение


async def save_rsi_alert(symbol, timeframe, rsi, ticker, candles, max_leverage):
    try:
        # Собираем дополнительные данные
        last_price = float(ticker.get('lastPrice', 0))
        volume_24h = float(ticker.get('amount24', 0))
        
        # Рассчитываем maxVol и maxVolPrice
        volumes = [float(v) for v in candles['vol']]
        max_vol = max(volumes) if volumes else 0
        max_vol_price = float(candles['close'][volumes.index(max_vol)]) if max_vol else 0
        
        # Новая функция расчета процентного соотношения
        def calculate_price_percentage(candles, timeframe, rsi_value):
            if not candles or 'high' not in candles or 'low' not in candles or 'close' not in candles:
                return None, None
                
            closes = [float(c) for c in candles['close']]
            if not closes:
                return None, None
                
            close_price = closes[-1]
            lookback = 16 if timeframe == 'Min15' else 4
            
            if rsi_value >= RSI_UPPER_THRESHOLD:
                # Ищем минимальный минимум
                lows = [float(l) for l in candles['low'][-lookback:]]
                extreme = min(lows) if lows else 0
                percent = ((close_price / extreme) - 1) * 100 if extreme != 0 else 0
            elif rsi_value <= RSI_THRESHOLD:
                # Ищем максимальный максимум
                highs = [float(h) for h in candles['high'][-lookback:]]
                extreme = max(highs) if highs else 0
                percent = ((close_price / extreme) - 1) * 100 if close_price != 0 and extreme != 0 else 0
            else:
                return None, None
                
            return round(percent, 2), round(extreme, 4)

        # Расчет процентного соотношения
        percent_price, extreme_value = calculate_price_percentage(candles, timeframe, rsi)
        
        alert_data = {
            "symbol": symbol,
            "last_price": last_price,
            "max_leverage": max_leverage,
            "volume_24h": volume_24h,
            "timeframe": timeframe,
            "rsi_value": rsi,
            "candles": {
                "time": candles['time'],
                "open": candles['open'],
                "high": candles['high'],
                "low": candles['low'],
                "close": candles['close'],
                "vol": candles['vol']
            },
            "max_vol": max_vol,
            "max_vol_price": max_vol * last_price,
            "percent_price": percent_price,
            "extreme_value": extreme_value
        }

        # Сохраняем в файл
        with open(DETECTION_FILE, 'a') as f:
            f.write(json.dumps(alert_data) + '\n')

    except Exception as e:
        print(f"Ошибка при сохранении данных для {symbol}: {str(e)}")


async def get_futures_data():
    start_time = time.time()
    print(f"[{time.strftime('%H:%M:%S')}] Начало получения данных...")

    # 1. Получаем список всех фьючерсных пар
    detail_url = "https://contract.mexc.com/api/v1/contract/detail"
    response = requests.get(detail_url)

    if response.status_code != 200:
        print("Ошибка при получении данных контрактов")
        return []

    contracts = response.json().get('data', [])

    # 2. Фильтруем пары с плечом >= 100
    filtered_pairs = [
        {
            'symbol': c['symbol'],
            'max_leverage': c['maxLeverage'],
            'display_name': c['displayName']
        }
        for c in contracts
        if c.get('maxLeverage', 0) >= 100
    ]

    print(
        f"[{time.strftime('%H:%M:%S')}] Получено {len(contracts)} контрактов, после фильтрации осталось {len(filtered_pairs)}")

    # 3. Получаем ВСЕ тикеры одним запросом
    print(f"[{time.strftime('%H:%M:%S')}] Загрузка данных тикеров...")
    ticker_url = "https://contract.mexc.com/api/v1/contract/ticker"
    ticker_response = requests.get(ticker_url)

    if ticker_response.status_code != 200:
        print(f"! Ошибка {ticker_response.status_code} при получении тикеров")
        return []

    tickers = {t['symbol']: t for t in ticker_response.json().get('data', [])}
    print(f"[{time.strftime('%H:%M:%S')}] Получено {len(tickers)} тикеров")

    # 4. Сопоставляем данные и получаем RSI только для отфильтрованных пар
    result = []
    async with aiohttp.ClientSession() as session:
        for pair in filtered_pairs:
            symbol = pair['symbol']
            ticker = tickers.get(symbol)
            rsi_values = {}  # Инициализируем словарь здесь

            if not ticker:
                print(f"! Отсутствуют данные для {symbol}")
                continue

            # Проверяем формат символа
            if '_' not in symbol:
                print(f"! Некорректный формат символа: {symbol}")
                continue

            # Асинхронные запросы для всех таймфреймов
            async def get_rsi_for_timeframe(session, symbol, timeframe):
                try:
                    url = f"https://contract.mexc.com/api/v1/contract/kline/{symbol}?interval={timeframe}&limit=100"
                    async with session.get(url) as response:
                        if response.status == 200:
                            data = await response.json()
                            if 'data' not in data:
                                return 'FormatErr', None

                            candles = data['data']
                            if not all(key in candles for key in ['time', 'open', 'high', 'low', 'close']):
                                return 'CandleErr', None

                            closes = [float(price) for price in candles['close'] if price]
                            # closes = closes[::-1]  # Закомментирована инверсия массива

                            if len(closes) >= 14:
                                rsi = calculate_rsi(closes)
                                return round(rsi, 2), candles  # Возвращаем свечи
                            return 'N/A', candles
                        return f'HTTP_{response.status}', None
                except Exception as e:
                    print(f"Ошибка при получении RSI {timeframe} для {symbol}: {str(e)}")
                    return 'Error', None

            tasks = []
            for timeframe in ['Min15', 'Hour4']:
                task = asyncio.create_task(
                    get_rsi_for_timeframe(session, symbol, timeframe)
                )
                tasks.append(task)

            results = await asyncio.gather(*tasks)
            for (timeframe, (rsi, candles)) in zip(['Min15', 'Hour4'], results):
                rsi_values[f'RSI_{timeframe}'] = rsi

                if isinstance(rsi, (int, float)) and (rsi <= RSI_THRESHOLD or rsi >= RSI_UPPER_THRESHOLD):
                    detected_assets.append({
                        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                        'symbol': symbol,
                        'rsi_value': rsi,
                        'timeframe': timeframe
                    })
                    await save_rsi_alert(symbol, timeframe, rsi, ticker, candles,
                                         pair['max_leverage'])  # Передаем max_leverage

            result.append({
                'Пара': pair['display_name'],
                'Символ': symbol,
                'Макс. плечо': pair['max_leverage'],
                'Объем 24h': round(float(ticker.get('amount24', 0))),
                **rsi_values
            })

    # Сохраняем историю в корректном JSON формате
    with open(HISTORY_FILE, 'w') as f:
        json.dump(detected_assets, f, indent=2)
    return result


# Запуск и вывод результатов
if __name__ == "__main__":
    while True:  # Добавляем бесконечный цикл
        try:
            # Очищаем только файл алертов
            open(DETECTION_FILE, 'w').close()

            # Загружаем существующую историю
            try:
                with open(HISTORY_FILE, 'r') as f:
                    detected_assets = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                detected_assets = []

            data = asyncio.run(get_futures_data())
            if not data:
                print("Нет данных для отображения")
            else:
                print("\n" + "="*80)
                print(f"[{time.strftime('%H:%M:%S')}] Обновление данных")
                print("{:30} | {:15} | {:>12} | {:>15} | {:>15}".format(
                    "Название", "Символ", "Плечо", "Объем 24h (USDT)", "RSI_Min15", "RSI_Hour4"
                ))
                print("-" * 80)

                for item in sorted(data, key=lambda x: x['Объем 24h'], reverse=True):
                    print("{:30} | {:15} | {:12.0f}x | {:15,.2f} | {:>15} | {:>15}".format(
                        item['Пара'],
                        item['Символ'],
                        item['Макс. плечо'],
                        item['Объем 24h'],
                        item['RSI_Min15'],
                        item['RSI_Hour4']
                    ))
            
            # Пауза между итерациями (5 минут)
            time.sleep(120)
            
        except Exception as e:
            print(f"Произошла ошибка: {str(e)}")
            time.sleep(60)  # Пауза перед повторной попыткой