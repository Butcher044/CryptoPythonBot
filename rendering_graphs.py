import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd
import json
import os
import time
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# Создаем директорию для графиков, если её нет
if not os.path.exists('image'):
    os.makedirs('image')

def process_alerts():
    try:
        # Загружаем данные из JSON файла
        with open('rsi_alerts.json', 'r') as file:
            json_lines = file.readlines()

        # Обрабатываем каждую строку JSON
        for line in json_lines:
            try:
                # Парсим JSON из строки
                json_data = json.loads(line)
                
                # Получаем символ и данные свечей
                symbol = json_data['symbol']
                candles_data = json_data['candles']

                # Создаем список словарей для DataFrame
                data = []
                for i in range(len(candles_data['time'])):
                    data.append({
                        "time": pd.Timestamp(candles_data['time'][i], unit='s'),
                        "open": candles_data['open'][i],
                        "high": candles_data['high'][i],
                        "low": candles_data['low'][i],
                        "close": candles_data['close'][i]
                    })

                # Преобразуем данные в DataFrame
                df = pd.DataFrame(data)
                df["time"] = pd.to_datetime(df["time"])
                df.set_index("time", inplace=True)

                # Настройка стиля графика
                style = mpf.make_mpf_style(base_mpf_style='charles', rc={'axes.edgecolor': 'none', 'axes.linewidth': 0})

                # Создание фигуры с нужным разрешением
                fig, axlist = mpf.plot(df, type='candle', style=style, title='', ylabel='', volume=False, 
                                     show_nontrading=False, returnfig=True, figsize=(19.2, 10.8))

                # Устанавливаем черный фон
                fig.patch.set_facecolor('black')
                for ax in axlist:
                    ax.set_facecolor('black')
                    ax.set_xticks([])
                    ax.set_yticks([])
                    ax.spines['top'].set_visible(False)
                    ax.spines['bottom'].set_visible(False)
                    ax.spines['left'].set_visible(False)
                    ax.spines['right'].set_visible(False)

                # Сохранение графика
                plt.savefig(f'image/{symbol}.png', dpi=100, bbox_inches='tight', pad_inches=0, facecolor='black')
                plt.close(fig)
                print(f"[{time.strftime('%H:%M:%S')}] Создан график для {symbol}")

            except Exception as e:
                print(f"Ошибка при обработке строки JSON: {str(e)}")
                continue

    except Exception as e:
        print(f"Ошибка при чтении файла: {str(e)}")

class AlertFileHandler(FileSystemEventHandler):
    def on_modified(self, event):
        if event.src_path.endswith('rsi_alerts.json'):
            print(f"\n[{time.strftime('%H:%M:%S')}] Обнаружены изменения в rsi_alerts.json")
            process_alerts()

if __name__ == "__main__":
    print(f"[{time.strftime('%H:%M:%S')}] Запуск мониторинга rsi_alerts.json...")
    
    # Создаем наблюдателя
    observer = Observer()
    observer.schedule(AlertFileHandler(), path='.', recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        observer.join()