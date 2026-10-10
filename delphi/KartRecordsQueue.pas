unit KartRecordsQueue;

{ Bounded latest-snapshot queue. No Synchronize or UI callbacks. Delphi 7. }
interface
uses Windows, Classes, KartRecordsSender;
type
  TKartRecordsQueue = class(TThread)
  private
    FLock: TRTLCriticalSection;
    FLockReady: Boolean;
    FWake, FStop: THandle;
    FHost: string;
    FPort, FNext: Integer;
    FPending: array[0..1] of AnsiString;
    FResponse, FError: string;
  protected
    procedure Execute; override;
  public
    constructor Create(const AHost: string; APort: Integer);
    destructor Destroy; override;
    procedure Submit(const ACategory, APeriod: string;
      const AChildren, AWomen, AMen: TKartRecord);
    procedure ReadStatus(var AResponse, AError: string);
  end;

implementation
uses SysUtils;

constructor TKartRecordsQueue.Create(const AHost: string; APort: Integer);
begin
  inherited Create(True);
  FreeOnTerminate := False;
  InitializeCriticalSection(FLock);
  FLockReady := True;
  FWake := CreateEvent(nil, False, False, nil);
  FStop := CreateEvent(nil, True, False, nil);
  if (FWake = 0) or (FStop = 0) then
    raise Exception.Create('Cannot create sender events');
  FHost := AHost; FPort := APort; FNext := 0;
  Resume;
end;

destructor TKartRecordsQueue.Destroy;
begin
  Terminate;
  if FStop <> 0 then SetEvent(FStop);
  if FWake <> 0 then SetEvent(FWake);
  { Resume a never-started thread after constructor failure before inherited join. }
  if Suspended then Resume;
  WaitFor;
  if FWake <> 0 then CloseHandle(FWake);
  if FStop <> 0 then CloseHandle(FStop);
  if FLockReady then DeleteCriticalSection(FLock);
  inherited Destroy;
end;

procedure TKartRecordsQueue.Submit(const ACategory, APeriod: string;
  const AChildren, AWomen, AMen: TKartRecord);
var Index: Integer; Packet: AnsiString;
begin
  Packet := BuildRecordsMessage(ACategory, APeriod, AChildren, AWomen, AMen);
  if ACategory = 'records_day' then Index := 0 else Index := 1;
  EnterCriticalSection(FLock);
  try FPending[Index] := Packet;
  finally LeaveCriticalSection(FLock); end;
  SetEvent(FWake);
end;

procedure TKartRecordsQueue.ReadStatus(var AResponse, AError: string);
begin
  EnterCriticalSection(FLock);
  try AResponse := FResponse; AError := FError;
  finally LeaveCriticalSection(FLock); end;
end;

procedure TKartRecordsQueue.Execute;
var Index: Integer; Packet: AnsiString; Response, Error: string; Accepted: Boolean;
begin
  while not Terminated do
  begin
    Packet := '';
    EnterCriticalSection(FLock);
    try
      Index := FNext;
      if FPending[Index] = '' then Index := 1 - Index;
      Packet := FPending[Index]; FPending[Index] := ''; FNext := 1 - Index;
    finally LeaveCriticalSection(FLock); end;
    if Packet = '' then begin WaitForSingleObject(FWake, 100); Continue; end;
    Accepted := SendJsonPacket(FHost, FPort, Packet, Response, Error, FStop);
    EnterCriticalSection(FLock);
    try
      FResponse := Response; FError := Error;
      { Never overwrite a newer submitted snapshot with a failed older one. }
      if not Accepted and (FPending[Index] = '') then FPending[Index] := Packet;
    finally LeaveCriticalSection(FLock); end;
    if not Accepted then WaitForSingleObject(FStop, 1000);
  end;
end;

end.
