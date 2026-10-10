unit KartRecordsSender;
{$B-}

{ Delphi 7 compatible TCP sender for Kart Racing Display.

  Protocol: one UTF-8 JSON object followed by LF.
  Server default: 127.0.0.1:8765.
}

interface

uses Windows;

type
  TKartRecord = record
    HasValue: Boolean;
    Driver: WideString;
    LapTime: Double;
    Kart: WideString;
  end;

function EmptyKartRecord: TKartRecord;
function BuildRecordsMessage(const ACategory, APeriod: string;
  const AChildren, AWomen, AMen: TKartRecord): AnsiString;
function SendJsonPacket(const AHost: string; APort: Integer;
  const AData: AnsiString; var AServerResponse, AErrorText: string;
  AStopEvent: THandle = 0): Boolean;
function SendRecordsForPeriod(const AHost: string; APort: Integer;
  const ACategory, APeriod: string;
  const AChildren, AWomen, AMen: TKartRecord;
  var AServerResponse, AErrorText: string): Boolean;

function MakeKartRecord(const ADriver: WideString; ALapTime: Double;
  const AKart: WideString): TKartRecord;

function SendDayRecords(const AHost: string; APort: Integer;
  const AChildren, AWomen, AMen: TKartRecord;
  var AServerResponse, AErrorText: string): Boolean;

function SendMonthRecords(const AHost: string; APort: Integer;
  const AChildren, AWomen, AMen: TKartRecord;
  var AServerResponse, AErrorText: string): Boolean;

function SendRecords(const AHost: string; APort: Integer;
  const ACategory: string;
  const AChildren, AWomen, AMen: TKartRecord;
  var AServerResponse, AErrorText: string): Boolean;

implementation

uses
  WinSock, SysUtils, Math, KartJsonReply;

const
  CSocketTimeoutMs = 5000;

function MakeKartRecord(const ADriver: WideString; ALapTime: Double;
  const AKart: WideString): TKartRecord;
begin
  Result.HasValue := True;
  Result.Driver := ADriver;
  Result.LapTime := ALapTime;
  Result.Kart := AKart;
end;

function EmptyKartRecord: TKartRecord;
begin
  Result.HasValue := False;
  Result.Driver := ''; Result.Kart := ''; Result.LapTime := 0;
end;

function JsonEscape(const AValue: WideString): WideString;
var
  I: Integer;
  C: WideChar;
begin
  Result := '';
  for I := 1 to Length(AValue) do
  begin
    C := AValue[I];
    case Ord(C) of
      8:  Result := Result + '\b';
      9:  Result := Result + '\t';
      10: Result := Result + '\n';
      12: Result := Result + '\f';
      13: Result := Result + '\r';
      34: Result := Result + '\"';
      92: Result := Result + '\\';
    else
      if Ord(C) < 32 then
        Result := Result + '\u' + WideString(IntToHex(Ord(C), 4))
      else
        Result := Result + C;
    end;
  end;
end;

function FloatToJson(AValue: Double): string;
begin
  if IsNan(AValue) or IsInfinite(AValue) or (AValue <= 0) then
    raise EConvertError.Create('Record time must be finite and positive');
  Result := FloatToStrF(AValue, ffGeneral, 15, 0);
  Result := StringReplace(Result, ',', '.', [rfReplaceAll]);
end;

function RecordToJson(const ARecord: TKartRecord): WideString;
begin
  if not ARecord.HasValue then begin Result := 'null'; Exit; end;
  if (Trim(ARecord.Driver) = '') or (Length(ARecord.Driver) > 256) or (Length(ARecord.Kart) > 256) then
    raise EConvertError.Create('Record name/kart is empty or too long');
  Result := '{"name":"' + JsonEscape(ARecord.Driver) +
    '","time":' + WideString(FloatToJson(ARecord.LapTime));
  if ARecord.Kart <> '' then
    Result := Result + ',"kart":"' + JsonEscape(ARecord.Kart) + '"';
  Result := Result + '}';
end;

function BuildRecordsMessage(const ACategory, APeriod: string;
  const AChildren, AWomen, AMen: TKartRecord): AnsiString;
var
  Json: WideString;
  PeriodKey, Updated: string;
  Y, M, D, I: Integer;
  ST: TSystemTime;
begin
  if ACategory = 'records_day' then PeriodKey := 'date'
  else if ACategory = 'records_month' then PeriodKey := 'month'
  else raise EConvertError.Create('Unsupported record category');
  if ((PeriodKey = 'date') and (Length(APeriod) <> 10)) or
     ((PeriodKey = 'month') and (Length(APeriod) <> 7)) then
    raise EConvertError.Create('Invalid records period');
  for I := 1 to Length(APeriod) do
    if I in [5, 8] then begin
      if APeriod[I] <> '-' then raise EConvertError.Create('Invalid period separators');
    end else if not (APeriod[I] in ['0'..'9']) then
      raise EConvertError.Create('Invalid period digits');
  Y := StrToInt(Copy(APeriod, 1, 4)); M := StrToInt(Copy(APeriod, 6, 2)); D := 1;
  if PeriodKey = 'date' then D := StrToInt(Copy(APeriod, 9, 2));
  EncodeDate(Y, M, D);
  GetSystemTime(ST);
  Updated := FormatDateTime('yyyy"-"mm"-"dd"T"hh":"nn":"ss"Z"', SystemTimeToDateTime(ST));
  Json := '{"category":"' + WideString(ACategory) + '","data":{' +
    '"schema_version":1,"' + WideString(PeriodKey) + '":"' + WideString(APeriod) +
    '","updated_at":"' + WideString(Updated) + '",' +
    '"children":' + RecordToJson(AChildren) + ',' +
    '"women":' + RecordToJson(AWomen) + ',' +
    '"men":' + RecordToJson(AMen) + '}}' + WideString(#10);
  Result := AnsiString(UTF8Encode(Json));
end;

function ResolveIPv4(const AHost: string; var AAddress: u_long): Boolean;
var
  Host, Part: string;
  I, Start, Count, Value: Integer;
begin
  Result := False; AAddress := 0; Host := AHost;
  if SameText(Host, 'localhost') then Host := '127.0.0.1';
  if (Length(Host) < 7) or (Length(Host) > 15) then Exit;
  Start := 1; Count := 0;
  for I := 1 to Length(Host) + 1 do
  begin
    if I <= Length(Host) then
      if not (Host[I] in ['0'..'9', '.']) then Exit;
    if (I > Length(Host)) or (Host[I] = '.') then
    begin
      Part := Copy(Host, Start, I - Start);
      if (Length(Part) < 1) or (Length(Part) > 3) then Exit;
      Value := StrToInt(Part); if Value > 255 then Exit;
      AAddress := (AAddress shl 8) or u_long(Value);
      Inc(Count); Start := I + 1;
    end;
  end;
  Result := Count = 4;
  AAddress := htonl(AAddress);
end;

function RemainingMs(AStarted: DWORD): Integer;
var NowTick, Elapsed: DWORD;
begin
  NowTick := GetTickCount;
  if NowTick >= AStarted then Elapsed := NowTick - AStarted
  else Elapsed := (High(DWORD) - AStarted) + NowTick + 1;
  if Elapsed >= CSocketTimeoutMs then Result := 0
  else Result := CSocketTimeoutMs - Integer(Elapsed);
end;

function WaitSocket(ASocket: TSocket; AWrite: Boolean; AStarted: DWORD;
  AStopEvent: THandle): Boolean;
var Fds, Errors: TFDSet; TV: TTimeVal; WaitMs, Count: Integer;
begin
  Result := False;
  repeat
    if (AStopEvent <> 0) and (WaitForSingleObject(AStopEvent, 0) = WAIT_OBJECT_0) then Exit;
    WaitMs := RemainingMs(AStarted); if WaitMs = 0 then Exit;
    if WaitMs > 100 then WaitMs := 100;
    FD_ZERO(Fds); FD_SET(ASocket, Fds); FD_ZERO(Errors); FD_SET(ASocket, Errors);
    TV.tv_sec := 0; TV.tv_usec := WaitMs * 1000;
    if AWrite then Count := select(0, nil, @Fds, @Errors, @TV)
    else Count := select(0, @Fds, nil, @Errors, @TV);
    if Count = SOCKET_ERROR then Exit;
    if Count > 0 then begin Result := True; Exit; end;
  until False;
end;

function SendAll(ASocket: TSocket; const AData: AnsiString; AStarted: DWORD;
  AStopEvent: THandle): Boolean;
var
  SentNow, SentTotal, DataLength: Integer;
begin
  Result := False;
  SentTotal := 0;
  DataLength := Length(AData);
  while SentTotal < DataLength do
  begin
    if not WaitSocket(ASocket, True, AStarted, AStopEvent) then Exit;
    SentNow := send(ASocket, AData[SentTotal + 1], DataLength - SentTotal, 0);
    if SentNow = SOCKET_ERROR then begin
      if WSAGetLastError = WSAEWOULDBLOCK then Continue;
      Exit;
    end;
    if SentNow <= 0 then Exit;
    Inc(SentTotal, SentNow);
  end;
  Result := True;
end;

function ReceiveLine(ASocket: TSocket; var ALine: string; AStarted: DWORD;
  AStopEvent: THandle): Boolean;
var
  Buffer: array[0..511] of AnsiChar;
  Count, I: Integer;
  Data: AnsiString;
begin
  Result := False;
  ALine := '';
  Data := '';
  repeat
    if not WaitSocket(ASocket, False, AStarted, AStopEvent) then Exit;
    Count := recv(ASocket, Buffer, SizeOf(Buffer), 0);
    if (Count = SOCKET_ERROR) and (WSAGetLastError = WSAEWOULDBLOCK) then Continue;
    if Count <= 0 then
      Exit;
    for I := 0 to Count - 1 do
    begin
      if Buffer[I] = #10 then
      begin
        ALine := string(Data);
        if (Length(ALine) > 0) and (ALine[Length(ALine)] = #13) then
          Delete(ALine, Length(ALine), 1);
        Result := True;
        Exit;
      end;
      Data := Data + Buffer[I];
      if Length(Data) > 65536 then
        Exit;
    end;
  until False;
end;

function SendJsonPacket(const AHost: string; APort: Integer;
  const AData: AnsiString; var AServerResponse, AErrorText: string;
  AStopEvent: THandle): Boolean;
var
  WSAData: TWSAData;
  S: TSocket;
  Address: TSockAddrIn;
  IP: u_long;
  Started: DWORD;
  Mode: u_long;
  SocketError, ErrorSize, Code: Integer;
begin
  Result := False;
  AServerResponse := '';
  AErrorText := '';

  Started := GetTickCount;
  if (Length(AData) = 0) or (Length(AData) > 1048576) then
  begin
    AErrorText := 'Invalid packet size';
    Exit;
  end;
  if (AData[Length(AData)] <> #10) or (Pos(#10, Copy(AData, 1, Length(AData) - 1)) <> 0) then
  begin AErrorText := 'Packet must contain one JSON line ending in LF'; Exit; end;
  if (APort < 1) or (APort > 65535) then
  begin
    AErrorText := 'Invalid TCP port';
    Exit;
  end;
  if WSAStartup($0101, WSAData) <> 0 then
  begin
    AErrorText := 'WSAStartup failed';
    Exit;
  end;

  S := INVALID_SOCKET;
  try
    if not ResolveIPv4(AHost, IP) then
    begin
      AErrorText := 'Use numeric IPv4 or localhost; DNS is excluded from the bounded operation';
      Exit;
    end;

    S := socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if S = INVALID_SOCKET then
    begin
      AErrorText := 'socket failed: ' + IntToStr(WSAGetLastError);
      Exit;
    end;

    Mode := 1;
    if ioctlsocket(S, FIONBIO, Mode) = SOCKET_ERROR then
    begin AErrorText := 'Cannot enable nonblocking socket'; Exit; end;

    FillChar(Address, SizeOf(Address), 0);
    Address.sin_family := AF_INET;
    Address.sin_port := htons(APort);
    Address.sin_addr.S_addr := IP;
    if connect(S, Address, SizeOf(Address)) = SOCKET_ERROR then
    begin
      Code := WSAGetLastError;
      if (Code <> WSAEWOULDBLOCK) and (Code <> WSAEINPROGRESS) then
      begin AErrorText := 'connect failed: ' + IntToStr(Code); Exit; end;
      if not WaitSocket(S, True, Started, AStopEvent) then
      begin AErrorText := 'connect deadline/cancel'; Exit; end;
      SocketError := 0; ErrorSize := SizeOf(SocketError);
      if (getsockopt(S, SOL_SOCKET, SO_ERROR, PAnsiChar(@SocketError), ErrorSize) = SOCKET_ERROR) or (SocketError <> 0) then
      begin AErrorText := 'connect rejected: ' + IntToStr(SocketError); Exit; end;
    end;

    if not SendAll(S, AData, Started, AStopEvent) then
    begin
      AErrorText := 'send failed/deadline/cancel: ' + IntToStr(WSAGetLastError);
      Exit;
    end;
    if not ReceiveLine(S, AServerResponse, Started, AStopEvent) then
    begin
      AErrorText := 'response failed/deadline/cancel: ' + IntToStr(WSAGetLastError);
      Exit;
    end;
    if not ReplyAccepted(AnsiString(AServerResponse)) then
    begin
      AErrorText := 'Server rejected data: ' + AServerResponse;
      Exit;
    end;
    Result := True;
  finally
    if S <> INVALID_SOCKET then
      closesocket(S);
    WSACleanup;
  end;
end;

function SendRecordsForPeriod(const AHost: string; APort: Integer;
  const ACategory, APeriod: string;
  const AChildren, AWomen, AMen: TKartRecord;
  var AServerResponse, AErrorText: string): Boolean;
var Packet: AnsiString;
begin
  Result := False; AServerResponse := ''; AErrorText := '';
  try
    Packet := BuildRecordsMessage(ACategory, APeriod, AChildren, AWomen, AMen);
    Result := SendJsonPacket(AHost, APort, Packet, AServerResponse, AErrorText);
  except on E: Exception do AErrorText := E.Message; end;
end;

function SendRecords(const AHost: string; APort: Integer;
  const ACategory: string;
  const AChildren, AWomen, AMen: TKartRecord;
  var AServerResponse, AErrorText: string): Boolean;
var Period: string;
begin
  if ACategory = 'records_day' then Period := FormatDateTime('yyyy-mm-dd', Date)
  else Period := FormatDateTime('yyyy-mm', Date);
  Result := SendRecordsForPeriod(AHost, APort, ACategory, Period,
    AChildren, AWomen, AMen, AServerResponse, AErrorText);
end;

function SendDayRecords(const AHost: string; APort: Integer;
  const AChildren, AWomen, AMen: TKartRecord;
  var AServerResponse, AErrorText: string): Boolean;
begin
  Result := SendRecords(AHost, APort, 'records_day',
    AChildren, AWomen, AMen, AServerResponse, AErrorText);
end;

function SendMonthRecords(const AHost: string; APort: Integer;
  const AChildren, AWomen, AMen: TKartRecord;
  var AServerResponse, AErrorText: string): Boolean;
begin
  Result := SendRecords(AHost, APort, 'records_month',
    AChildren, AWomen, AMen, AServerResponse, AErrorText);
end;

end.
