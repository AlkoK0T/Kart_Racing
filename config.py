import os

class Config:
    DATA_FOLDER = os.getenv('DATA_FOLDER', 'data')
    PORT = int(os.getenv('PORT', 5000))
    DEBUG = os.getenv('DEBUG', 'False').lower() == 'true'
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key')
    UPDATE_TIME = int(os.getenv('UPDATE_TIME', 5000))
