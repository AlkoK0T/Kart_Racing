from flask import render_template, jsonify
from .utils import load_race_data, get_last_update_time
from config import Config
from . import app
import time
from datetime import datetime

# Глобальная переменная для хранения времени последнего изменения
last_update_time = 0

@app.route('/')
def show_results():
    race_data = load_race_data(Config.DATA_FOLDER)  # Передаем папку явно
    
    return render_template('results.html',
                         current_race=race_data.get('current_race') if race_data else None,
                         best_laps=race_data.get('best_laps', []) if race_data else [],
                         top_pilots=race_data.get('top_pilots', []) if race_data else [],
                         last_updated=race_data.get('last_updated', datetime.now().strftime('%Y-%m-%d %H:%M:%S')) if race_data else datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                         error_message="Данные заездов не найдены" if race_data is None else None)

@app.route('/check-updates')
def check_updates():
    global last_update_time
    current_update_time = get_last_update_time(Config.DATA_FOLDER)  # Передаем папку явно
    
    # Ждем изменения файлов (долгий опрос)
    timeout = 30  # Максимальное время ожидания (сек)
    start_time = time.time()
    
    while current_update_time <= last_update_time:
        if time.time() - start_time > timeout:
            return jsonify({'updated': False})
        time.sleep(1)
        current_update_time = get_last_update_time(Config.DATA_FOLDER)
    
    last_update_time = current_update_time
    return jsonify({'updated': True})