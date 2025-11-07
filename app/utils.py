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

            if not current_race and 'laps' in data and isinstance(data['laps'], dict):
                laps = data['laps']
                karts = list(laps.keys())

                best_times = {}
                avg_times = {}
                last_laps = {}
                total_laps = {}
                total_time = {}
                diff={}
                max_time = 0

                for kart in karts:
                    times = laps.get(kart, [])
                    if not times:
                        times = [0]
                        laps[kart] = times

                    best_times[kart] = min(times)
                    avg_times[kart] = sum(times) / len(times)
                    total_time[kart]=sum(times)
                    last_laps[kart] = times[-1]
                    total_laps[kart] = len(times)
                    max_time = max(max_time, len(times))
                    diff[kart] = times[-1]-times[-2] if len(times)>2 else 0

                # Глобально лучшее время
                global_best_time = min(best_times.values()) if best_times else 0

                # Разница от лучшего круга
                time_deltas = {
                    kart: best_time - global_best_time
                    for kart, best_time in best_times.items()
                }

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
                    'time_deltas': time_deltas,      
                    'last_laps': last_laps,   
                    'total_time' : total_time,     
                    'total_laps': total_laps,        
                    'max_time': max_time,
                    'diff' : diff,
                    'UPD_TIME': Config.UPDATE_TIME,
                    'type': 'current_race'
                }

        return {
            'current_race': current_race,
            'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }

    except Exception as e:
        print(f"Ошибка при загрузке данных: {e}")
        return None
