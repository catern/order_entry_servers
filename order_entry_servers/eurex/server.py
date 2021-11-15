from __future__ import annotations
import trio
import time
from rsyscall.sys.socket import SOCK
from rsyscall.epoller import AsyncReadBuffer
from order_entry_servers.eurex.protocol import *

@dataclasses.dataclass
class Fill:
    price: Decimal
    quantity: int

    def render(self) -> Dict[str, int]:
        return {'FillPx': decimal_to_price(self.price), 'FillQty': self.quantity}

@dataclasses.dataclass
class ServerOrder:
    connection: Connection
    cl_ord_id: ClOrdID
    new_order_single: ffi.CData
    fills: List[Fill]

    async def accept(self, canceled: bool=False) -> None:
        await self.connection.send('NewOrderResponseT', {
            'ResponseHeaderME': {
            },
            'ClOrdID': self.cl_ord_id.number,
            'OrdStatus': get_enum_bytes("OrdStatus", "Canceled" if canceled else "New"),
        })

    async def unsolicited_cancel(self) -> None:
        await self.connection.send('DeleteOrderBroadcastT', {
            'RBCHeaderME': {
            },
            'ClOrdID': self.cl_ord_id.number,
            'OrigClOrdID': self.cl_ord_id.number,
            'OrdStatus': get_enum_bytes("OrdStatus", "Canceled"),
        })

    def fill_status(self) -> str:
        filled = sum(fill.quantity for fill in self.fills)
        if filled == 0:
            return "New"
        elif filled < self.new_order_single.OrderQty:
            return "PartiallyFilled"
        else:
            return "Filled"

    async def accept_fill(self, price: Decimal, quantity: int) -> None:
        fills = [Fill(price, quantity)]
        self.fills.extend(fills)
        await self.connection.send('OrderExecResponseT', {
            'ClOrdID': self.cl_ord_id.number,
            'OrigClOrdID': self.cl_ord_id.number,
            'OrdStatus': get_enum_bytes("OrdStatus", self.fill_status()),
            'NoFills': len(fills),
            'FillsGrp': [fill.render() for fill in fills],
        })

    async def fill(self, price: Decimal, quantity: int) -> None:
        fills = [Fill(price, quantity)]
        self.fills.extend(fills)
        await self.connection.send('OrderExecNotificationT', {
            'RBCHeaderME': {
            },
            'ClOrdID': self.cl_ord_id.number,
            'OrigClOrdID': self.cl_ord_id.number,
            'OrdStatus': get_enum_bytes("OrdStatus", self.fill_status()),
            'NoFills': len(fills),
            'FillsGrp': [fill.render() for fill in fills],
        })

@dataclasses.dataclass
class Connection:
    server: Server
    buf: AsyncReadBuffer

    async def send(self, msg_type: str, fields: Dict[str, Any]) -> ffi.CData:
        msg = to_out_struct(msg_type, fields)
        await self.buf.fd.write_all_bytes(bytes(ffi.buffer(msg)))
        return msg

    async def recv(self, msg_type: str=None) -> ffi.CData:
        header = await self.buf.read_cffi('MessageHeaderInCompT', remove=False)
        msg = copy_cast(tid_to_type[header.TemplateID], await self.buf.read_length(header.BodyLen))
        if msg_type:
            assert ffi.typeof(msg) == ffi.typeof(msg_type)
        return msg

    @classmethod
    async def accept(cls, server: Server, nursery: trio.Nursery, sock: AsyncFileDescriptor) -> Connection:
        self = cls(
            server,
            AsyncReadBuffer(sock, parsing_ffi=ffi),
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
                    await self.send('UserLoginResponseT', {})
                elif type == ffi.typeof('NewOrderSingleShortRequestT'):
                    cl_ord_id = self.server._add_cl_ord_id(msg.ClOrdID)
                    self.server.orders.put(ServerOrder(self, cl_ord_id, msg, fills=[]))
                else:
                    raise Exception("got unhandled", msg, ps(msg))

@dataclasses.dataclass
class Server:
    listening: AsyncFileDescriptor
    cl_ord_ids: Dict[int, ClOrdID]
    orders: PersistentQueue[ServerOrder]

    @classmethod
    async def start(cls, nursery: trio.Nursery, listening: AsyncFileDescriptor) -> Server:
        self = cls(
            listening,
            {},
            PersistentQueue(),
        )
        nursery.start_soon(self._run)
        return self

    async def _run(self) -> None:
        async with trio.open_nursery() as nursery:
            while True:
                connected_sock = await self.listening.make_new_afd(await self.listening.accept(SOCK.NONBLOCK))
                await Connection.accept(self, nursery, connected_sock)

    def _add_cl_ord_id(self, number: int) -> ClOrdID:
        ret = ClOrdID(number)
        self.cl_ord_ids[number] = ret
        return ret
                
