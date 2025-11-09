from flask import render_template, jsonify, request
from .utils import load_race_data, get_last_update_time
from config import Config
from . import app
import time
from datetime import datetime
import threading


# Глобальная переменная для хранения времени последнего изменения и текущего вида
last_update_time = 0
current_view = 1
view_lock = threading.Lock()

@app.route('/')
def show_results():
    global current_view

    race_data = load_race_data(Config.DATA_FOLDER)  # Передаем папку явно
    match current_view:
        case 1:
            return render_template('results.html',
                                current_race=race_data.get('current_race') if race_data else None,
                                best_laps=race_data.get('best_laps', []) if race_data else [],
                                top_pilots=race_data.get('top_pilots', []) if race_data else [],
                                last_updated=race_data.get('last_updated', datetime.now().strftime('%Y-%m-%d %H:%M:%S')) if race_data else datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                error_message="Данные заездов не найдены" if race_data is None else None)
        case 2:
            return render_template('results_alt.html',
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
    return jsonify({'updated': last_update_time})   

@app.route('/state', methods=['GET', 'PUT'])
def state():
    global current_view
    if request.method == 'GET':
        with view_lock:
            return jsonify({'view': current_view})
    elif request.method == 'PUT':
        try:
            data = request.get_json()
            if not data:
                return jsonify({'error': 'No JSON payload'}), 400
            new_view = data.get('view')
            if new_view not in (1, 2):
                return jsonify({'error': 'view must be 1 or 2'}), 400
            with view_lock:
                current_view = new_view
            return jsonify({'success': True, 'view': current_view})
        except Exception as e:
            return jsonify({'error': str(e)}), 400
        
@app.route('/set_state')
def set_state():
    # Страница управления состоянием (видом) — возвращает HTML интерфейс
    return render_template('set_state.html')