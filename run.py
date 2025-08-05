from app import create_app
import os
from config import Config

app = create_app()

if __name__ == '__main__':
    data_folder = Config.DATA_FOLDER
    os.makedirs(data_folder, exist_ok=True)
    
    app.run(host='0.0.0.0', port=Config.PORT, debug=Config.DEBUG)