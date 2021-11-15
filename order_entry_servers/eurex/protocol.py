from __future__ import annotations
from order_entry_servers._raw import ffi, lib
from typing import *
import re
import dataclasses
from decimal import Decimal
import dneio

T = TypeVar('T')

@dataclasses.dataclass
class PersistentQueue(Generic[T]):
    data: List[T] = dataclasses.field(default_factory=list)
    idx: int = 0
    exc: Optional[Exception] = None
    _waiting_cbs: t.List[dneio.Continuation[None]] = dataclasses.field(default_factory=list)

    async def get(self) -> T:
        while self.idx >= len(self.data) and not self.exc:
            await dneio.shift(self._waiting_cbs.append)
        if self.idx >= len(self.data):
            raise self.exc
        self.idx += 1
        return self.data[self.idx-1]

    def put(self, val: T) -> None:
        self.data.append(val)
        waiting, self._waiting_cbs = self._waiting_cbs, []
        for cb in waiting:
            cb.send(None)

    def close(self, exc: Exception) -> None:
        self.exc = exc
        waiting, self._waiting_cbs = self._waiting_cbs, []
        for cb in waiting:
            cb.send(None)

@dataclasses.dataclass
class ClOrdID:
    number: int
    queue: PersistentQueue[ffi.CData] = dataclasses.field(default_factory=PersistentQueue)
        
def copy_cast(type: str, data: bytes) -> ffi.CData:
    # ffi.cast drops the reference to the backing buffer, so we have to allocate some space and copy into there
    buf = ffi.new(type + '*')
    ffi.memmove(buf, ffi.cast(type + '*', ffi.from_buffer(data)), min(ffi.sizeof(buf[0]), len(data)))
    return buf[0]

def camel_to_snake(name: str) -> str:
  name = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
  return re.sub('([a-z0-9])([A-Z])', r'\1_\2', name).lower()

def decimal_to_price(decimal: Decimal) -> int:
    scaled = decimal.scaleb(8)
    assert int(scaled) == scaled
    return int(scaled)

def price_to_decimal(price: int) -> Decimal:
    return Decimal(price).scaleb(-8)

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
                for name, field in typ.fields
                if not name.startswith('Pad')}
    elif typ.kind == 'pointer':
        return render(typ.item, val[0])
    else:
        raise Exception("wat dis", typ, typ.kind)

def ps(val) -> Any:
    return render(ffi.typeof(val), val)

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
