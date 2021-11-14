from __future__ import annotations
from order_entry_servers._raw import ffi, lib
from typing import *
import re
import dataclasses
from decimal import Decimal
        
def camel_to_snake(name: str) -> str:
  name = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
  return re.sub('([a-z0-9])([A-Z])', r'\1_\2', name).lower()

def get_tid(msg_type: str) -> int:
    return getattr(lib, 'TID_' + camel_to_snake(msg_type[:-1]).upper())

def _make_tid_to_type() -> Dict[int, str]:
    ret = {}
    for type in ffi.list_types()[0]:
        try:
            tid = get_tid(type)
        except AttributeError:
            pass
        else:
            ret[tid] = type
    return ret

tid_to_type = _make_tid_to_type()

def to_in_struct(msg_type: str, fields: Dict[str, Any]) -> ffi.CData:
    return ffi.new(msg_type + '*', {
        **fields,
        'MessageHeaderIn': {
            'BodyLen': ffi.sizeof(msg_type),
            'TemplateID': get_tid(msg_type),
        },
    })

def to_out_struct(msg_type: str, fields: Dict[str, Any]) -> ffi.CData:
    return ffi.new(msg_type + '*', {
        **fields,
        'MessageHeaderOut': {
            'BodyLen': ffi.sizeof(msg_type),
            'TemplateID': get_tid(msg_type),
        },
    })

def get_enum(name: str, val: str) -> bytes:
    return getattr(lib, '_'.join(['ENUM', camel_to_snake(name).upper(), camel_to_snake(val).upper()]))

def get_enum_bytes(name: str, val: str) -> bytes:
    return bytes([get_enum(name, val + 'Char')])

def render(typ, val: Any) -> Any:
    if typ.kind == 'primitive':
        return val
    elif typ.kind == 'array':
        ret = [render(typ.item, x) for x in val]
        if isinstance(ret[0], bytes):
            return b"".join(ret).rstrip(b'\0')
        else:
            return ret
    elif typ.kind == 'struct':
        return {name: render(field.type, getattr(val, name))
                for name, field in typ.fields}
    elif typ.kind == 'pointer':
        return render(typ.item, val[0])
    else:
        raise Exception("wat dis", typ, typ.kind)

def ps(val) -> Any:
    return render(ffi.typeof(val), val)

@dataclasses.dataclass
class Order:
    pass

import enum
class Side(enum.Enum):
    Buy = "buy"
    Sell = "sell"

class TimeInForce(enum.Enum):
    Day = "day"
    IOC = "ioc"

@dataclasses.dataclass
class User:
    id: int
    password: bytes
