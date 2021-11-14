from __future__ import annotations
import trio
import time
from rsyscall.sys.socket import SOCK
from rsyscall.epoller import AsyncReadBuffer
from order_entry_servers.eurex.protocol import *

@dataclasses.dataclass
class Connection:
    buf: AsyncReadBuffer
    seq_num: int = 1

    async def send(self, msg_type: str, fields: Dict[str, Any]) -> ffi.CData:
        seq_num = self.seq_num
        self.seq_num += 1
        msg = to_out_struct(msg_type, {
            **fields,
            'ResponseHeader': {
                'RequestTime': time.time_ns(),
                'SendingTime': time.time_ns(),
                'MsgSeqNum': seq_num,
            },
        })
        await self.buf.fd.write_all_bytes(bytes(ffi.buffer(msg)))
        return msg

    async def recv(self, msg_type: str=None) -> ffi.CData:
        data = await self.buf.read_length(ffi.sizeof('MessageHeaderInCompT'))
        header = ffi.cast('MessageHeaderInCompT*', ffi.from_buffer(data))
        data += await self.buf.read_length(header.BodyLen - len(data))
        msg = ffi.cast(tid_to_type[header.TemplateID] + '*', ffi.from_buffer(data))[0]
        if msg_type:
            assert ffi.typeof(msg) == ffi.typeof(msg_type)
        return msg

    @classmethod
    async def accept(cls, nursery: trio.Nursery, sock: AsyncFileDescriptor) -> Connection:
        self = cls(
            AsyncReadBuffer(sock),
        )
        logon = await self.recv('LogonRequestT')
        logon_response = await self.send('LogonResponseT', {
            'ThrottleTimeInterval': 5000,
            'ThrottleNoMsgs': 5000,
            'ThrottleDisconnectLimit': 0,
            'HeartBtInt': logon.HeartBtInt,
            'SessionInstanceID': 1234,
            'MarketID': get_enum('MARKETID', 'XEUR'),
            'TradSesMode': get_enum('TradSesMode', 'Simulated'),
            'DefaultCstmApplVerID': b'PyOES',
            'DefaultCstmApplVerSubID': b'D0002',
        })
        nursery.start_soon(self._run)

    async def _run(self) -> None:
        async with trio.open_nursery() as nursery:
            while True:
                msg = await self.recv()
                type = ffi.typeof(msg)
                if type == ffi.typeof('UserLoginRequestT'):
                    await self.send('UserLoginResponseT', {
                    })
                else:
                    raise Exception("got unhandled", msg, ps(msg))

@dataclasses.dataclass
class Server:
    listening: AsyncFileDescriptor

    @classmethod
    async def start(cls, nursery: trio.Nursery, listening: AsyncFileDescriptor) -> Server:
        self = cls(
            listening,
        )
        nursery.start_soon(self._run)
        return self

    async def _run(self) -> None:
        async with trio.open_nursery() as nursery:
            while True:
                connected_sock = await self.listening.make_new_afd(await self.listening.accept(SOCK.NONBLOCK))
                await Connection.accept(nursery, connected_sock)
                
