import os

class Config:
    DATA_FOLDER = os.getenv('DATA_FOLDER', 'data')
    PORT = int(os.getenv('PORT', 5000))
    DEBUG = os.getenv('DEBUG', 'False').lower() == 'true'
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key')
    UPDATE_TIME = int(os.getenv('UPDATE_TIME', 5000))
    FILE_POLL_INTERVAL = float(os.getenv('FILE_POLL_INTERVAL', 1))
    TCP_ENABLED = os.getenv('TCP_ENABLED', 'True').lower() == 'true'
    TCP_HOST = os.getenv('TCP_HOST', '127.0.0.1')
    TCP_PORT = int(os.getenv('TCP_PORT', 8765))
    SERIAL_PORT = os.getenv('SERIAL_PORT', '')
    SERIAL_BAUDRATE = int(os.getenv('SERIAL_BAUDRATE', 9600))
    SERIAL_RECONNECT_INTERVAL = float(os.getenv('SERIAL_RECONNECT_INTERVAL', 60))
    SERIAL_VID = int(os.environ['SERIAL_VID'], 16) if os.getenv('SERIAL_VID') else None
    SERIAL_PID = int(os.environ['SERIAL_PID'], 16) if os.getenv('SERIAL_PID') else None
    SERIAL_NUMBER = os.getenv('SERIAL_NUMBER', '')
    ODTEMP_ENABLED = os.getenv('ODTEMP_ENABLED', 'True').lower() == 'true'
    ODTEMP_PORT = os.getenv('ODTEMP_PORT', '')
    ODTEMP_SERIAL_NUMBER = os.getenv('ODTEMP_SERIAL_NUMBER', '')
    ODTEMP_VID = int(os.getenv('ODTEMP_VID', '0483'), 16)
    ODTEMP_PID = int(os.getenv('ODTEMP_PID', 'A26A'), 16)
    ODTEMP_STREAM_INTERVAL_MS = int(os.getenv('ODTEMP_STREAM_INTERVAL_MS', 2000))
    ODTEMP_WATCHDOG_TIMEOUT = float(os.getenv('ODTEMP_WATCHDOG_TIMEOUT', 120))
    ODTEMP_RECONNECT_INTERVAL = float(os.getenv('ODTEMP_RECONNECT_INTERVAL', 60))
    DISPLAY_ENABLED = os.getenv('DISPLAY_ENABLED', 'True').lower() == 'true'
    DISPLAY_CONFIG = os.getenv('DISPLAY_CONFIG', 'screens.yaml')
    CENTRE_TIMEZONE = os.getenv('CENTRE_TIMEZONE', 'local')
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    LOG_FILE = os.getenv('LOG_FILE', 'karting.log')
    LOG_MAX_BYTES = int(os.getenv('LOG_MAX_BYTES', 5 * 1024 * 1024))
    LOG_BACKUP_COUNT = int(os.getenv('LOG_BACKUP_COUNT', 3))
