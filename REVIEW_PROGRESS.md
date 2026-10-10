# Исправления по CODE_REVIEW.md

Работа выполняется по одному пункту. После подготовки изменений и проверки
коммит ожидает подтверждения пользователя; следующий пункт до этого не начинается.

## CR-01 — подготовлен, ожидает подтверждения коммита

Изменения:

- Общий контроллер `app/static/js/polling.js` вместо трёх копий JavaScript.
- HTML передаёт через JSON ревизию отрисованного снимка, веб-подвид и интервал.
- Polling с `category=current_race` не считает обновления часов/температуры
  изменением таблицы. API без category сохраняет прежнюю семантику.
- Подключение контроллера в обоих вариантах кругов, гонке и универсальной таблице.
- Удалены неопределённая переменная `сurrent_race.UPD_TIME` и чтение отсутствующего
  timestamp. Подвид сравнивается с style1/style2, а не именем шаблона race.
- `PUT /state` допускает только style1/style2.
- Единственная отложенная перезагрузка, отсутствие параллельных запросов внутри
  одного цикла, timeout/backoff, отмена при уходе и восстановление при pageshow.
- Добавлены Flask/unit, исполняемые JS-регрессии и четыре Behave-сценария.

Проверено 2026-10-10:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
$env:LOG_FILE=''
.\.venv\Scripts\python.exe -m behave --format progress
```

Результат: 13 unit-тестов успешно (включая запуск 12 JS-тестов в Node.js);
7 features, 48 сценариев, 187 Behave-шагов успешно. JS проверялся с подменёнными
DOM/fetch/таймерами; настоящий браузер и работающий производственный сервер не
запускались.

Сообщение подготовленного коммита:

```text
fix(web): correct revision polling and share the update controller (CR-01)

- Bootstrap polling from the rendered race revision and selected subview
- Filter race updates so clock and temperature changes do not reload the page
- Replace duplicated scripts with one timeout/retry-aware controller
- Restrict web subviews to style1/style2 and prevent duplicate reloads
- Add Flask, JavaScript and Behave regressions; document the polling contract
```

## Остальные пункты

CR-02–CR-21 не начаты. В частности, глобальная ревизия удаления, гонки
Condition.wait и изоляция Flask-приложений остаются работой CR-09; текущий патч
не заявляет исправления этих проблем. Нулевые круги, восстановление источников,
store/lifecycle, Pygame и Delphi также не изменены этим пунктом.
