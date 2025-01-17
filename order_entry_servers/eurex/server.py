from __future__ import annotations
import trio
import time
from rsyscall.sys.socket import SOCK
from rsyscall.epoller import AsyncReadBuffer
from order_entry_servers.eurex.protocol import *
import logging

logger = logging.getLogger(__name__)

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

    def __post_init__(self) -> None:
        dneio.reset(self._run())

    async def _run(self) -> None:
        while True:
            msg = await self.cl_ord_id.queue.get()
            type = ffi.typeof(msg).cname
            if type in ['DeleteOrderSingleRequestT']:
                await self.connection.send_application_response('DeleteOrderResponseT', {
                    'ResponseHeaderME': {
                    },
                    'ClOrdID': msg.ClOrdID,
                    'OrigClOrdID': msg.OrigClOrdID,
                    'OrdStatus': get_enum_bytes("OrdStatus", "PendingCancel"),
                })
                await self.unsolicited_cancel()
            else:
                raise Exception(self, "got unhandled", msg, ps(msg))

    async def accept(self, canceled: bool=False) -> None:
        await self.connection.send_application_response('NewOrderResponseT', {
            'ClOrdID': self.cl_ord_id.number,
            'OrdStatus': get_enum_bytes("OrdStatus", "Canceled" if canceled else "New"),
        })

    async def unsolicited_cancel(self) -> None:
        await self.connection.send_application_notification('DeleteOrderBroadcastT', {
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
        await self.connection.send_application_response('OrderExecResponseT', {
            'ClOrdID': self.cl_ord_id.number,
            'OrigClOrdID': self.cl_ord_id.number,
            'OrdStatus': get_enum_bytes("OrdStatus", self.fill_status()),
            'NoFills': len(fills),
            'FillsGrp': [fill.render() for fill in fills],
        })

    async def fill(self, price: Decimal, quantity: int) -> None:
        fills = [Fill(price, quantity)]
        self.fills.extend(fills)
        await self.connection.send_application_notification('OrderExecNotificationT', {
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
    next_seq_num: int = 1

    async def _send_msg(self, msg: ffi.CData) -> ffi.CData:
        await self.buf.fd.write_all_bytes(bytes(ffi.buffer(msg)))
        return msg

    async def send_session_response(self, msg_type: str, fields: Dict[str, Any]) -> ffi.CData:
        msg = to_out_struct(msg_type, {
            **fields,
            'ResponseHeader': {
                'RequestTime': time.time_ns(),
                'SendingTime': time.time_ns(),
            },
        })
        return await self._send_msg(msg)

    async def send_application_response(self, msg_type: str, fields: Dict[str, Any]) -> ffi.CData:
        msg = to_out_struct(msg_type, {
            **fields,
            'ResponseHeaderME': {
                'RequestTime': time.time_ns(),
                'SendingTime': time.time_ns(),
                'ApplID': get_enum("APPLID", "SessionData"),
                'ApplMsgID': str(len(self.server.appl_msgs * 3)).encode().rjust(16, b" "),
            }
        })
        self.server.appl_msgs.append(msg)
        return await self._send_msg(msg)

    async def send_application_notification(self, msg_type: str, fields: Dict[str, Any]) -> ffi.CData:
        msg = to_out_struct(msg_type, {
            **fields,
            # TODO I don't know what RBC or ME stand for...
            'RBCHeaderME': {
                'SendingTime': time.time_ns(),
                'ApplID': get_enum("APPLID", "SessionData"),
                'ApplMsgID': str(len(self.server.appl_msgs * 3)).encode().rjust(16, b" "),
            }
        })
        self.server.appl_msgs.append(msg)
        return await self._send_msg(msg)

    async def recv(self, msg_type: str=None) -> ffi.CData:
        header = await self.buf.read_cffi('MessageHeaderInCompT', remove=False)
        msg = copy_cast(tid_to_type[header.TemplateID], await self.buf.read_length(header.BodyLen))
        if msg_type:
            assert ffi.typeof(msg) == ffi.typeof(msg_type)
        assert self.next_seq_num == msg.RequestHeader.MsgSeqNum, (
            f"{self.next_seq_num} != {msg.RequestHeader.MsgSeqNum}")
        self.next_seq_num += 1
        return msg

    @classmethod
    async def accept(cls, server: Server, nursery: trio.Nursery, sock: AsyncFileDescriptor) -> Connection:
        self = cls(
            server,
            AsyncReadBuffer(sock, parsing_ffi=ffi),
        )
        logon = await self.recv('LogonRequestT')
        logon_response = await self.send_session_response('LogonResponseT', {
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
        while True:
            try:
                msg = await self.recv()
            except EOFError:
                break
            type = ffi.typeof(msg).cname
            if type == 'UserLoginRequestT':
                await self.send_session_response('UserLoginResponseT', {})
            elif type == 'NewOrderSingleShortRequestT':
                cl_ord_id = self.server._add_cl_ord_id(msg.ClOrdID)
                self.server.orders.put(ServerOrder(self, cl_ord_id, msg, fills=[]))
            elif type == 'DeleteOrderSingleRequestT':
                self.server.cl_ord_ids[msg.OrigClOrdID].queue.put(msg)
                self.server.orders.put(ServerOrder(self, cl_ord_id, msg, fills=[]))
            elif type == "RetransmitMEMessageRequestT":
                for start_idx, appl_msg in enumerate(self.server.appl_msgs):
                    header = extract_appl_header(appl_msg)
                    assert header is not None
                    if b"".join(header.ApplMsgID) > b"".join(msg.ApplBegMsgID):
                        logger.info("Retransmitting starting with %s", b"".join(header.ApplMsgID))
                        break
                else:
                    # nothing to retransmit!
                    start_idx = len(self.server.appl_msgs)
                to_retransmit = self.server.appl_msgs[start_idx:start_idx+100]
                end = extract_appl_header(to_retransmit[-1]).ApplMsgID if to_retransmit else b"\0"*16
                last = extract_appl_header(self.server.appl_msgs[-1]).ApplMsgID if self.server.appl_msgs else b"\0"*16
                await self.send_session_response('RetransmitMEMessageResponseT', {
                    'ApplTotalMessageCount': len(to_retransmit),
                    'ApplEndMsgID': end,
                    'RefApplLastMsgID': last,
                })
                for appl_msg in to_retransmit:
                    await self._send_msg(appl_msg)
            else:
                raise Exception("got unhandled", msg, ps(msg))

@dataclasses.dataclass
class Server:
    listening: AsyncFileDescriptor
    cl_ord_ids: Dict[int, ClOrdID]
    orders: PersistentQueue[ServerOrder]
    appl_msgs: List[ffi.CData]

    @classmethod
    async def start(cls, nursery: trio.Nursery, listening: AsyncFileDescriptor) -> Server:
        self = cls(
            listening,
            {},
            PersistentQueue(),
            [],
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
                
