## Технологии

- Python 3.10+
- Flask
- PyYAML
- HTML5/CSS3
- JavaScript

## Установка

1. Клонируйте репозиторий:
```bash
git clone https://github.com/your-username/karting-results.git
cd karting-results
```

2. Создайте виртуальное окружение:
```bash
python -m venv venv
source venv/bin/activate  # Linux/MacOS
venv\Scripts\activate    # Windows
```

3. Установите зависимости:
```bash
pip install -r requirements.txt
```

4. Создайте файл конфигурации:
```bash
cp .env.example .env
```

5. Отредактируйте .env файл при необходимости:
```env
DATA_FOLDER=data
PORT=5000
DEBUG=True
```

6. Запустите приложение:
```bash
python run.py
```

Приложение будет доступно по адресу: http://localhost:5000

## Добавление данных

Для обновления результатов создайте YAML-файлы в папке `data`:

### 1. Текущий заезд (пример)
```yaml
laps:
  kart_01: [32.45, 31.89, 32.12, 31.76]
  kart_02: [33.12, 32.45, 32.89, 32.34]
  kart_03: [31.98, 31.45, 31.67, 31.23]
```

### 2. Лучшие круги (пример)
```yaml
best_laps:
  - [kart_01, 31.76]
  - [kart_02, 32.34]
  - [kart_03, 31.23]
```

### 3. Топ пилотов (пример)
```yaml
top_pilots:
  - [Иванов И., 31.23]
  - [Петров П., 31.45]
  - [Сидоров С., 31.76]
```

## Структура проекта

```
karting-results/
├── app/
│   ├── __init__.py       - Инициализация приложения
│   ├── routes.py         - Маршруты Flask
│   └── data_loader.py    - Загрузка и обработка данных
├── tests/
│   └── test_app.py       - Тесты приложения
├── templates/
│   └── results.html      - HTML шаблон
├── data/                 - Папка с данными (создается автоматически)
├── config.py             - Конфигурация приложения
├── run.py                - Точка входа
├── requirements.txt      - Зависимости
├── .env.example          - Пример конфигурации окружения
└── README.md             - Документация
```

## Тестирование

Запуск тестов:
```bash
pytest tests/
```

## Развертывание

### Heroku
```bash
heroku create your-app-name
git push heroku main
```

### PythonAnywhere
1. Загрузите файлы через веб-интерфейс
2. Настройте виртуальное окружение
3. Укажите путь к WSGI-файлу: `/var/www/yourusername_pythonanywhere_com_wsgi.py`

### Docker
```Dockerfile
FROM python:3.10-slim

WORKDIR /app
COPY . .

RUN pip install -r requirements.txt

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "run:app"]
```

