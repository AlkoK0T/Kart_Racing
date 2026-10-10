# Отправка рекордов из Delphi 7

Добавьте `KartRecordsSender.pas`, `KartJsonReply.pas` и, для GUI,
`KartRecordsQueue.pas`. Только стандартные библиотеки Delphi/WinSock.
`EmptyKartRecord` передаёт null; MakeKartRecord требует конечное время > 0.
Снимок содержит schema_version=1, date/month и updated_at в UTC.

```pascal
var
  Kids, Women, Men: TKartRecord;
  Response, ErrorText: string;
begin
  Kids := MakeKartRecord('Маша', 31.2, '7');
  Women := EmptyKartRecord;
  Men := EmptyKartRecord;

  if not SendRecordsForPeriod('127.0.0.1', 8765, 'records_day', '2026-10-10',
    Kids, Women, Men, Response, ErrorText) then
    ShowMessage(ErrorText);
end;
```

Для месячных рекордов вызовите `SendMonthRecords`. Если Python-приложение работает
на другом компьютере, замените `127.0.0.1` его IPv4-адресом и настройте
`TCP_HOST=0.0.0.0`. Сервер возвращает `{"ok":true}` либо JSON с описанием ошибки.

Предпочитайте SendRecordsForPeriod с явно заданным периодом центра. Старые
SendDayRecords/SendMonthRecords используют локальную дату компьютера отправителя.
Общий deadline connect/send/receive — 5 секунд; только IPv4/localhost, без DNS.
Ответ ограничен 64 КиБ и разбирается как JSON, не поиском подстроки.
Синхронные функции не вызывайте из обработчиков GUI. Создайте один
TKartRecordsQueue при запуске формы и вызывайте:

```pascal
Sender.Submit('records_day', '2026-10-10', Kids, Women, Men);
Sender.Submit('records_month', '2026-10', Kids, Women, Men);
```

Очередь хранит максимум один ожидающий снимок каждого вида, заменяет его новым,
повторяет неудачи с паузой 1 секунду. ReadStatus копирует последний ответ/ошибку;
UI-callback нет. При закрытии Sender.Free отменяет сеть (проверка каждые 100 мс)
и присоединяет worker. Не отправляйте после Free. Успех не означает долговременного
хранения: Python хранит снимки в памяти.

Исходники .pas и .dpr ASCII; кириллица в примере задана WideString(#$...).
Для настоящей сборки и loopback/deadline/cancel тестов:

```powershell
$env:DELPHI_DCC32='C:\Delphi7\Bin\dcc32.exe'
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_delphi_contract.py -v
```

Без этой переменной тесты явно skipped. Найденный Embarcadero 23.0 отказал
в CLI-сборке из-за лицензии; реальная компиляция Delphi 7 пока не подтверждена.
