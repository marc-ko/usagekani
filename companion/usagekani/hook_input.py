"""Read only hook metadata, streaming past arbitrarily large tool responses."""
import json

FIELDS={'session_id','hook_event_name','tool_name','tool_use_id','tool_input',
        'permission_mode','elicitation_id','request_id','mcp_server_name','mode'}


class _Reader:
    def __init__(self,stream):self.stream=stream;self.buffer='';self.pos=0
    def peek(self):
        if self.pos==len(self.buffer):self.buffer=self.stream.read(8192);self.pos=0
        return self.buffer[self.pos] if self.buffer else ''
    def get(self):
        char=self.peek()
        if char:self.pos+=1
        return char
    def space(self):
        while self.peek() and self.peek().isspace():self.get()
    def value(self,capture):
        self.space(); first=self.peek(); kept=[]; size=0
        if not first:raise ValueError('Incomplete hook')
        quoted=False;escaped=False;depth=0;started=False
        while self.peek():
            char=self.peek()
            if started and not quoted and depth==0 and (char in ',}]' or char.isspace()):break
            char=self.get();started=True
            if capture:
                size+=1
                if size<=131072:kept.append(char)
            if quoted:
                if escaped:escaped=False
                elif char=='\\':escaped=True
                elif char=='"':quoted=False
            elif char=='"':quoted=True
            elif char in '[{':depth+=1
            elif char in ']}':
                depth-=1
                if depth<0:raise ValueError('Invalid hook value')
            if not quoted and depth==0 and (first=='"' or first in '[{'):break
        if quoted or depth:raise ValueError('Incomplete hook value')
        return json.loads(''.join(kept)) if capture and size<=131072 else None


def read_hook_payload(stream):
    reader=_Reader(stream);reader.space()
    if reader.get()!='{':raise ValueError('Hook must be an object')
    result={}
    for _ in range(64):
        reader.space()
        if reader.peek()=='}':reader.get();return result
        key=reader.value(True)
        if not isinstance(key,str):raise ValueError('Invalid hook key')
        reader.space()
        if reader.get()!=':':raise ValueError('Invalid hook key')
        value=reader.value(key in FIELDS)
        if key in FIELDS:result[key]=value
        reader.space(); separator=reader.get()
        if separator=='}':return result
        if separator!=',':raise ValueError('Invalid hook separator')
    raise ValueError('Too many hook fields')
