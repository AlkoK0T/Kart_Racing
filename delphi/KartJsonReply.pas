unit KartJsonReply;

{ Delphi 7 syntax; strict top-level JSON boolean, not a substring search. }
interface
function ReplyAccepted(const AText: AnsiString): Boolean;

implementation
uses SysUtils;

type
  TReplyParser = class
  private
    FText: AnsiString;
    FPos, FDepth: Integer;
    function Peek: AnsiChar;
    procedure Space;
    procedure Expect(C: AnsiChar);
    procedure Literal(const S: AnsiString);
    function ReadString: WideString;
    procedure Number;
    procedure Value;
  public
    constructor Create(const S: AnsiString);
    function Accepted: Boolean;
  end;

constructor TReplyParser.Create(const S: AnsiString);
begin
  inherited Create;
  FText := S; FPos := 1; FDepth := 0;
end;

function TReplyParser.Peek: AnsiChar;
begin
  if FPos <= Length(FText) then Result := FText[FPos] else Result := #0;
end;

procedure TReplyParser.Space;
begin
  while Peek in [#9, #10, #13, ' '] do Inc(FPos);
end;

procedure TReplyParser.Expect(C: AnsiChar);
begin
  Space;
  if Peek <> C then raise EConvertError.Create('Invalid JSON response');
  Inc(FPos);
end;

procedure TReplyParser.Literal(const S: AnsiString);
begin
  if Copy(FText, FPos, Length(S)) <> S then
    raise EConvertError.Create('Invalid JSON literal');
  Inc(FPos, Length(S));
end;

function TReplyParser.ReadString: WideString;
var C: AnsiChar; I, Code: Integer;
begin
  Result := ''; Expect('"');
  while Peek <> '"' do
  begin
    C := Peek; Inc(FPos);
    if Ord(C) < 32 then raise EConvertError.Create('Invalid JSON string');
    if C = '\' then
    begin
      C := Peek; Inc(FPos);
      case C of
        '"', '\', '/': Result := Result + WideChar(C);
        'b': Result := Result + #8;
        'f': Result := Result + #12;
        'n': Result := Result + #10;
        'r': Result := Result + #13;
        't': Result := Result + #9;
        'u': begin
          if FPos + 3 > Length(FText) then raise EConvertError.Create('Short Unicode escape');
          for I := FPos to FPos + 3 do
            if not (FText[I] in ['0'..'9', 'a'..'f', 'A'..'F']) then
              raise EConvertError.Create('Invalid Unicode escape');
          Code := StrToInt('$' + string(Copy(FText, FPos, 4)));
          Inc(FPos, 4); Result := Result + WideChar(Code);
        end;
      else raise EConvertError.Create('Invalid JSON escape');
      end;
    end
    else Result := Result + WideChar(C);
  end;
  Inc(FPos);
end;

procedure TReplyParser.Number;
begin
  if Peek = '-' then Inc(FPos);
  if Peek = '0' then Inc(FPos)
  else begin
    if not (Peek in ['1'..'9']) then raise EConvertError.Create('Invalid JSON number');
    while Peek in ['0'..'9'] do Inc(FPos);
  end;
  if Peek = '.' then begin
    Inc(FPos);
    if not (Peek in ['0'..'9']) then raise EConvertError.Create('Invalid fraction');
    while Peek in ['0'..'9'] do Inc(FPos);
  end;
  if Peek in ['e', 'E'] then begin
    Inc(FPos); if Peek in ['+', '-'] then Inc(FPos);
    if not (Peek in ['0'..'9']) then raise EConvertError.Create('Invalid exponent');
    while Peek in ['0'..'9'] do Inc(FPos);
  end;
end;

procedure TReplyParser.Value;
begin
  Space; Inc(FDepth);
  try
    if FDepth > 32 then raise EConvertError.Create('JSON response too deep');
    case Peek of
      '"': ReadString;
      't': Literal('true');
      'f': Literal('false');
      'n': Literal('null');
      '{': begin
        Expect('{'); Space;
        if Peek <> '}' then repeat
          ReadString; Expect(':'); Value; Space;
          if Peek <> ',' then Break;
          Inc(FPos);
        until False;
        Expect('}');
      end;
      '[': begin
        Expect('['); Space;
        if Peek <> ']' then repeat
          Value; Space;
          if Peek <> ',' then Break;
          Inc(FPos);
        until False;
        Expect(']');
      end;
    else Number;
    end;
  finally Dec(FDepth); end;
end;

function TReplyParser.Accepted: Boolean;
var Key: WideString; Seen: Boolean;
begin
  Result := False; Seen := False; Expect('{'); Space;
  if Peek <> '}' then repeat
    Key := ReadString; Expect(':'); Space;
    if Key = 'ok' then begin
      if Seen then raise EConvertError.Create('Duplicate ok field');
      Seen := True;
      if Peek = 't' then begin Literal('true'); Result := True; end
      else begin Literal('false'); Result := False; end;
    end else Value;
    Space;
    if Peek <> ',' then Break;
    Inc(FPos);
  until False;
  Expect('}'); Space;
  if FPos <= Length(FText) then raise EConvertError.Create('Trailing JSON data');
  Result := Result and Seen;
end;

function ReplyAccepted(const AText: AnsiString): Boolean;
var Parser: TReplyParser;
begin
  Result := False;
  if Length(AText) > 65536 then Exit;
  Parser := TReplyParser.Create(AText);
  try
    try Result := Parser.Accepted;
    except on E: EConvertError do Result := False; end;
  finally Parser.Free; end;
end;

end.
