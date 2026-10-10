from dataclasses import dataclass, fields
import math
import os
from pathlib import Path

from karting.timezones import display_timezone

PROJECT_ROOT = Path(__file__).resolve().parent


@dataclass
class Config:
    DATA_FOLDER: str = str(PROJECT_ROOT / 'data')
    PORT: int = 5000
    DEBUG: bool = False
    SECRET_KEY: str = 'dev-secret-key'
    UPDATE_TIME: int = 5000
    FILE_POLL_INTERVAL: float = 1
    TCP_ENABLED: bool = True
    TCP_HOST: str = '127.0.0.1'
    TCP_PORT: int = 8765
    TCP_MAX_CLIENTS: int = 16
    TCP_CLIENT_TIMEOUT: float = 30
    TCP_FRAME_TIMEOUT: float = 10
    SERIAL_PORT: str = ''
    SERIAL_BAUDRATE: int = 9600
    SERIAL_RECONNECT_INTERVAL: float = 60
    SERIAL_VID: int | None = None
    SERIAL_PID: int | None = None
    SERIAL_NUMBER: str = ''
    ODTEMP_ENABLED: bool = True
    ODTEMP_PORT: str = ''
    ODTEMP_SERIAL_NUMBER: str = ''
    ODTEMP_VID: int = 0x0483
    ODTEMP_PID: int = 0xA26A
    ODTEMP_STREAM_INTERVAL_MS: int = 2000
    ODTEMP_WATCHDOG_TIMEOUT: float = 120
    ODTEMP_RECONNECT_INTERVAL: float = 60
    DISPLAY_ENABLED: bool = True
    DISPLAY_CONFIG: str = str(PROJECT_ROOT / 'screens.yaml')
    CENTRE_TIMEZONE: str = 'local'
    LOG_LEVEL: str = 'INFO'
    LOG_FILE: str = str(PROJECT_ROOT / 'karting.log')
    LOG_MAX_BYTES: int = 5 * 1024 * 1024
    LOG_BACKUP_COUNT: int = 3

    @classmethod
    def from_env(cls, environ=None):
        environ = os.environ if environ is None else environ
        values = {}
        hex_fields = ('SERIAL_VID', 'SERIAL_PID', 'ODTEMP_VID', 'ODTEMP_PID')
        for item in fields(cls):
            if item.name not in environ and not (item.name == 'DEBUG' and 'KART_DEBUG' in environ):
                continue
            raw = environ.get('KART_DEBUG', environ.get('DEBUG')) if item.name == 'DEBUG' else environ[item.name]
            try:
                if item.name in hex_fields:
                    values[item.name] = int(raw, 16) if raw else None
                elif item.type is bool:
                    if item.name == 'DEBUG' and raw.lower() in ('release', 'debug'):
                        values[item.name] = raw.lower() == 'debug'
                        continue
                    if raw.lower() not in ('true', 'false', '1', '0', 'yes', 'no', 'on', 'off'):
                        raise ValueError('must be a boolean')
                    values[item.name] = raw.lower() in ('true', '1', 'yes', 'on')
                elif item.type in (int, float):
                    values[item.name] = item.type(raw)
                else:
                    values[item.name] = raw
            except (ValueError, TypeError) as exc:
                name = 'KART_DEBUG' if item.name == 'DEBUG' and 'KART_DEBUG' in environ else item.name
                raise ValueError(f'{name}: invalid value {raw!r}') from exc
        return cls(**values)

    def __post_init__(self):
        for item in fields(self):
            if item.type is str and not isinstance(getattr(self, item.name), str):
                raise ValueError(f'{item.name}: must be a string')
        for name, low, high in (('PORT', 1, 65535), ('TCP_PORT', 0, 65535),
                               ('UPDATE_TIME', 100, 3600000), ('TCP_MAX_CLIENTS', 1, 1024),
                               ('SERIAL_BAUDRATE', 1, 4000000), ('ODTEMP_STREAM_INTERVAL_MS', 200, 600000),
                               ('LOG_MAX_BYTES', 1, 1024 * 1024 * 1024), ('LOG_BACKUP_COUNT', 0, 100)):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f'{name}: must be an integer between {low} and {high}')
        for name in ('FILE_POLL_INTERVAL', 'TCP_CLIENT_TIMEOUT', 'TCP_FRAME_TIMEOUT',
                     'SERIAL_RECONNECT_INTERVAL', 'ODTEMP_WATCHDOG_TIMEOUT', 'ODTEMP_RECONNECT_INTERVAL'):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 86400:
                raise ValueError(f'{name}: must be finite, positive and at most 86400 seconds')
        if self.ODTEMP_WATCHDOG_TIMEOUT < 1:
            raise ValueError('ODTEMP_WATCHDOG_TIMEOUT: minimum 1 second')
        if self.ODTEMP_STREAM_INTERVAL_MS * 2 > self.ODTEMP_WATCHDOG_TIMEOUT * 1000:
            raise ValueError('ODTEMP_STREAM_INTERVAL_MS/ODTEMP_WATCHDOG_TIMEOUT: period must not exceed half watchdog timeout')
        for name in ('TCP_ENABLED', 'DEBUG', 'ODTEMP_ENABLED', 'DISPLAY_ENABLED'):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f'{name}: must be boolean')
        for name in ('SERIAL_VID', 'SERIAL_PID', 'ODTEMP_VID', 'ODTEMP_PID'):
            value = getattr(self, name)
            if value is None and name.startswith('SERIAL_'):
                continue
            if type(value) is not int or not 0 <= value <= 65535:
                raise ValueError(f'{name}: must be a USB ID between 0000 and FFFF')
        if (self.SERIAL_VID is None) != (self.SERIAL_PID is None):
            raise ValueError('SERIAL_VID/SERIAL_PID: configure both USB IDs')
        if self.ODTEMP_ENABLED and self.ODTEMP_PORT and self.SERIAL_PORT:
            normalize = lambda value: value.strip().casefold().removeprefix('\\\\.\\')
            if normalize(self.ODTEMP_PORT) == normalize(self.SERIAL_PORT):
                raise ValueError('SERIAL_PORT/ODTEMP_PORT: same port cannot be owned by both modules')
        if self.LOG_LEVEL.upper() not in ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'):
            raise ValueError('LOG_LEVEL: unknown logging level')
        if not isinstance(self.TCP_HOST, str) or not self.TCP_HOST.strip():
            raise ValueError('TCP_HOST: must be non-empty')
        display_timezone(self.CENTRE_TIMEZONE)
        for name in ('DATA_FOLDER', 'DISPLAY_CONFIG', 'LOG_FILE'):
            value = getattr(self, name)
            if name == 'LOG_FILE' and not value:
                continue
            if not isinstance(value, str) or not value:
                raise ValueError(f'{name}: must be a non-empty path')
            path = Path(value)
            setattr(self, name, str(path if path.is_absolute() else PROJECT_ROOT / path))
