import os
import yaml
from datetime import datetime
from config import Config

def get_last_update_time(data_folder=Config.DATA_FOLDER):
    """Возвращает время последнего изменения любого YAML-файла"""
    try:
        yaml_files = [f for f in os.listdir(data_folder) if f.endswith('.yaml')]
        if not yaml_files:
            return 0
        
        newest_file = max(yaml_files, key=lambda f: os.path.getmtime(os.path.join(data_folder, f)))
        return os.path.getmtime(os.path.join(data_folder, newest_file))
    except Exception as e:
        print(f"Ошибка при проверке времени обновления: {e}")
        return 0

def load_race_data(data_folder=Config.DATA_FOLDER):
    """Загружает и анализирует данные из YAML-файлов"""
    try:
        yaml_files = [f for f in os.listdir(data_folder) if f.endswith('.yaml')]
        if not yaml_files:
            return None
        
        current_race = None
        best_laps = []
        top_pilots = []
        
        # Сортируем файлы по времени изменения (новые первыми)
        sorted_files = sorted(
            yaml_files, 
            key=lambda f: os.path.getmtime(os.path.join(data_folder, f)), 
            reverse=True
        )
        
        for filename in sorted_files:
            filepath = os.path.join(data_folder, filename)
            with open(filepath, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            
            # Обработка данных текущего заезда
            if not current_race and 'laps' in data and isinstance(data['laps'], dict):
                laps = data['laps']
                karts = list(laps.keys())
                
                # Вычисляем итоговое время и места
                total_times = {}
                for kart in karts:
                    total_times[kart] = round(sum(laps[kart]), 2)
                
                # Определяем места картов
                sorted_karts = sorted(total_times.items(), key=lambda x: x[1])
                positions = {}
                for position, (kart, _) in enumerate(sorted_karts, start=1):
                    positions[kart] = position
                
                current_race = {
                    'karts': karts,
                    'laps': laps,
                    'total_times': total_times,
                    'positions': positions,
                    'type': 'current_race'
                }
                
                # Формируем список лучших кругов из текущего заезда
                best_laps = [(kart, min(times)) for kart, times in laps.items()]
            
            # Обработка данных лучших кругов
            if not best_laps and 'best_laps' in data and isinstance(data['best_laps'], list):
                best_laps = data['best_laps']
            
            # Обработка данных топ пилотов
            if not top_pilots and 'top_pilots' in data and isinstance(data['top_pilots'], list):
                top_pilots = data['top_pilots']
        
        # Сортируем и ограничиваем количество записей
        return {
            'current_race': current_race,
            'best_laps': sorted(best_laps, key=lambda x: x[1])[:5] if best_laps else [],
            'top_pilots': sorted(top_pilots, key=lambda x: x[1])[:5] if top_pilots else [],
            'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
    except Exception as e:
        print(f"Ошибка при загрузке данных: {e}")
        return None