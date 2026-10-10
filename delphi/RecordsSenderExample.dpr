program RecordsSenderExample;

{$APPTYPE CONSOLE}

uses
  SysUtils,
  KartRecordsSender in 'KartRecordsSender.pas';

var
  ChildrenRecord, WomenRecord, MenRecord: TKartRecord;
  Response, ErrorText: string;
begin
  { ASCII source avoids dependence on the Delphi 7 editor code page. }
  ChildrenRecord := MakeKartRecord(WideString(#$041C#$0430#$0448#$0430), 31.2, '7');
  WomenRecord := EmptyKartRecord;
  MenRecord := EmptyKartRecord;

  { Console example only; use TKartRecordsQueue in a GUI. }
  if SendRecordsForPeriod('127.0.0.1', 8765, 'records_day',
    FormatDateTime('yyyy-mm-dd', Date),
    ChildrenRecord, WomenRecord, MenRecord, Response, ErrorText) then
    Writeln('OK: ', Response)
  else
    Writeln('ERROR: ', ErrorText);

  Readln;
end.
