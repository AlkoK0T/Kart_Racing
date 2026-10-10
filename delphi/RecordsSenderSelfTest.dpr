program RecordsSenderSelfTest;
{$APPTYPE CONSOLE}
uses Windows, SysUtils, KartRecordsSender, KartRecordsQueue, KartJsonReply;
var Child, Empty: TKartRecord; Packet: AnsiString; Response, Error: string;
    Written: DWORD; Queue: TKartRecordsQueue; Started: DWORD;
procedure Check(Value: Boolean);
begin if not Value then raise Exception.Create('Self-test failed'); end;
begin
  try
    Empty := EmptyKartRecord;
    Child := MakeKartRecord(WideString(#$041C#$0430#$0448#$0430), 35.123, '7');
    Packet := BuildRecordsMessage('records_day', '2026-10-10', Child, Empty, Empty);
    Check(Pos('"women":null', Packet) > 0);
    Check(ReplyAccepted('{"ok":true,"data":[null,1.2e-3,{"x":"\\"}]}'));
    Check(not ReplyAccepted('{"ok":false,"error":"ok: true"}'));
    Check(not ReplyAccepted('{"data":{"ok":true}}'));
    Check(not ReplyAccepted('{"ok":true,"ok":false}'));
    Check(not ReplyAccepted('{"ok":true} garbage'));
    Check(not ReplyAccepted('{"ok":true,}'));
    Check(not ReplyAccepted('{"ok":"true"}'));
    Check(not ReplyAccepted('{"ok":true,"x":"\q"}'));
    if ParamStr(1) = '--send' then begin
      Check(SendJsonPacket('127.0.0.1', StrToInt(ParamStr(2)), Packet, Response, Error));
    end else if ParamStr(1) = '--cancel' then begin
      Queue := TKartRecordsQueue.Create('127.0.0.1', StrToInt(ParamStr(2)));
      Started := GetTickCount;
      try Queue.Submit('records_day', '2026-10-10', Child, Empty, Empty); Sleep(200);
      finally Queue.Free; end;
      Check(GetTickCount - Started < 2000);
    end else if ParamStr(1) = '--deadline' then begin
      Started := GetTickCount;
      Check(not SendJsonPacket('127.0.0.1', StrToInt(ParamStr(2)), Packet, Response, Error));
      Check(GetTickCount - Started < 6000);
    end;
    WriteFile(GetStdHandle(STD_OUTPUT_HANDLE), Packet[1], Length(Packet), Written, nil);
  except on E: Exception do begin Writeln(E.Message); Halt(1); end; end;
end.
