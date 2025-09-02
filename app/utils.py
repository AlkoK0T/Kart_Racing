import os
from datetime import datetime
import yaml
from config import Config

def get_last_update_time(data_folder):
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
        
        # Сортируем файлы по времени изменения (новые первыми)
        sorted_files = sorted(
            yaml_files, 
            key=lambda f: os.path.getmtime(os.path.join(data_folder, f)), 
            reverse=True
        )
        
        for filename in sorted_files:
            filepath = os.path.join(data_folder, filename)
            with open(filepath, 'r', encoding='cp1251', errors='ignore') as f:
                data = yaml.safe_load(f)
            
            # Обработка данных текущего заезда
            if not current_race and 'laps' in data and isinstance(data['laps'], dict):
                laps = data['laps']
                karts = list(laps.keys())
                
                # Вычисляем лучшее время и среднее время для каждого карта
                best_times = {}
                avg_times = {}
                max_time = 0
                for kart in karts:
                    if not laps[kart]:
                        laps[kart]=[0]
                    times = laps[kart]
                    best_times[kart] = min(times)
                    max_time = max(max_time,len(times)) 
                    avg_times[kart] = sum(times) / len(times)
                
                # Определяем места картов по лучшему времени
                sorted_karts = sorted(best_times.items(), key=lambda x: x[1])
                positions = {}
                for position, (kart, _) in enumerate(sorted_karts, start=1):
                    positions[kart] = position
                
                current_race = {
                    'karts': karts,
                    'laps': laps,
                    'best_times': best_times,
                    'avg_times': avg_times,
                    'positions': positions,
                    'max_time' : max_time,
                    'UPD_TIME' : Config.UPDATE_TIME,
                    'type': 'current_race'
                }
                

        
        # Сортируем и ограничиваем количество записей
        return {
            'current_race': current_race,
            'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
    except Exception as e:
        print(f"Ошибка при загрузке данных: {e}")
        return None