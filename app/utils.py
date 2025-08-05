import os
import time
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

def load_race_data(data_folder):
    try:
        yaml_files = [f for f in os.listdir(data_folder) if f.endswith('.yaml')]
        if not yaml_files:
            return None
        
        current_race = None
        best_laps = []
        top_pilots = []
        
        for filename in sorted(yaml_files, key=lambda f: os.path.getmtime(os.path.join(data_folder, f)), reverse=True):
            filepath = os.path.join(data_folder, filename)
            with open(filepath, 'r') as f:
                data = yaml.safe_load(f)
            
            # ... остальная логика обработки данных ...
        
        return {
            'current_race': current_race,
            'best_laps': sorted(best_laps, key=lambda x: x[1])[:5],
            'top_pilots': sorted(top_pilots, key=lambda x: x[1])[:5],
            'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
    except Exception as e:
        print(f"Ошибка при загрузке данных: {e}")
        return None