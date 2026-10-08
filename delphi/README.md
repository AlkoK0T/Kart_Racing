# Отправка рекордов из Delphi 7

Добавьте `KartRecordsSender.pas` в Delphi-проект и включите `KartRecordsSender` в
секцию `uses`. Модуль использует только стандартные `Windows`, `WinSock` и
`SysUtils`, формирует UTF-8 JSON и отправляет одну строку на TCP-порт 8765.

```pascal
var
  Kids, Women, Men: TKartRecord;
  Response, ErrorText: string;
begin
  Kids := MakeKartRecord('Маша', 31.2, '7');
  Women := MakeKartRecord('Анна', 29.8, '3');
  Men := MakeKartRecord('Иван', 28.4, '12');

  if not SendDayRecords('127.0.0.1', 8765,
    Kids, Women, Men, Response, ErrorText) then
    ShowMessage(ErrorText);
end;
```

Для месячных рекордов вызовите `SendMonthRecords`. Если Python-приложение работает
на другом компьютере, замените `127.0.0.1` его IPv4-адресом и настройте
`TCP_HOST=0.0.0.0`. Сервер возвращает `{"ok":true}` либо JSON с описанием ошибки.
