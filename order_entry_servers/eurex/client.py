from __future__ import annotations
import trio
import time
from order_entry_servers.eurex.protocol import *
from rsyscall import AsyncFileDescriptor
from rsyscall.epoller import AsyncReadBuffer
from rsyscall.sys.socket import SHUT
import logging

logger = logging.getLogger(__name__)

@dataclasses.dataclass
class Fill:
    price: Decimal
    quantity: int
    msg: ffi.CData

class OrderDead(Exception):
    pass

class OrderCanceled(OrderDead):
    pass

class OrderFilled(OrderDead):
    pass

@dataclasses.dataclass
class Cancel:
    order: Order
    cl_ord_id: ClOrdID

    def __post_init__(self) -> None:
        dneio.reset(self._run())

    async def _run(self) -> None:
        while True:
            msg = await self.cl_ord_id.queue.get()
            raise Exception(self, "got unexpected message for cancel")

@dataclasses.dataclass
class Order:
    client: Client
    cl_ord_id: ClOrdID
    new_order_single: ffi.CData
    fills: PersistentQueue[Fill]

    def __post_init__(self) -> None:
        dneio.reset(self._run())

    @property
    def price(self) -> Decimal:
        return price_to_decimal(self.new_order_single.Price)

    @property
    def quantity(self) -> int:
        return self.new_order_single.OrderQty

    async def cancel(self) -> None:
        cl_ord_id = self.client._allocate_cl_ord_id()
        cancel_order_single = await self.client.send('DeleteOrderSingleRequestT', {
            'ClOrdID': cl_ord_id.number,
            'OrigClOrdID': self.cl_ord_id.number,
        })
        msg = await cl_ord_id.queue.get()
        type = ffi.typeof(msg).cname
        if type != 'DeleteOrderResponseT':
            raise Exception(self, "got unexpected response to cancel", msg, ps(msg))
        return Cancel(self, cl_ord_id)

    async def _run(self) -> None:
        while True:
            msg = await self.cl_ord_id.queue.get()
            type = ffi.typeof(msg).cname
            if hasattr(msg, 'FillsGrp'):
                for i in range(msg.NoFills):
                    fill = msg.FillsGrp[i]
                    self.fills.put(Fill(price_to_decimal(fill.FillPx), fill.FillQty, msg))
            if hasattr(msg, 'OrdStatus'):
                status = b''.join(msg.OrdStatus)
                if status == get_enum_bytes('OrdStatus', 'Canceled'):
                    self.fills.close(OrderCanceled())
                elif status == get_enum_bytes('OrdStatus', 'Filled'):
                    self.fills.close(OrderFilled())
                elif status in [get_enum_bytes('OrdStatus', 'New'), get_enum_bytes('OrdStatus', 'PartiallyFilled')]:
                    pass
                else:
                    raise Exception(self, "got unhandled OrdStatus", status)
            if type in ['OrderExecNotificationT', 'OrderExecResponseT', 'NewOrderResponseT', 'DeleteOrderBroadcastT']:
                # handled entirely by the introspection
                pass
            else:
                raise Exception(self, "got unhandled", msg, ps(msg))

@dataclasses.dataclass
class Client:
    buf: AsyncReadBuffer
    users: List[User]
    cl_ord_ids: Dict[int, ClOrdID]
    seq_num: int = 1
    last_appl_msg_id: bytes = b"\0"*16

    async def send(self, msg_type: str, fields: Dict[str, Any]) -> ffi.CData:
        seq_num = self.seq_num
        self.seq_num += 1
        msg = to_in_struct(msg_type, {
            **fields,
            'RequestHeader': {'MsgSeqNum': seq_num},
        })
        await self.buf.fd.write_all_bytes(bytes(ffi.buffer(msg)))
        return msg

    def _got_appl_msg_header(self, hdr: ffi.CData) -> None:
        appl_msg_id = b"".join(hdr.ApplMsgID)
        assert self.last_appl_msg_id < appl_msg_id, (
            f"{self.last_appl_msg_id} >= {appl_msg_id}")
        # we're not subscribing to any other streams
        assert hdr.ApplID == get_enum("APPLID", "SessionData")
        self.last_appl_msg_id = appl_msg_id

    async def recv(self, msg_type: str=None) -> ffi.CData:
        header = await self.buf.read_cffi('MessageHeaderOutCompT', remove=False)
        msg = copy_cast(tid_to_type[header.TemplateID], await self.buf.read_length(header.BodyLen))
        if msg_type:
            assert ffi.typeof(msg) == ffi.typeof(msg_type)
        appl_header = extract_appl_header(msg)
        if appl_header:
            self._got_appl_msg_header(appl_header)
        return msg

    @classmethod
    async def connect(cls, nursery: trio.Nursery, sock: AsyncFileDescriptor, users: List[User]) -> Client:
        self = Client(
            AsyncReadBuffer(sock, parsing_ffi=ffi),
            users=users,
            cl_ord_ids={},
        )
        logon = await self.send('LogonRequestT', {
            'RequestHeader': {'MsgSeqNum': 1},
            'HeartBtInt': 5000, # try for 5 seconds, gateway may override it
            'PartyIDSessionID': 1234,
            'DefaultCstmApplVerID': b'TODO',
            'Password': b'password',
            'ApplUsageOrders': get_enum_bytes('ApplUsageOrders', 'AutoSelect'),
            'ApplUsageQuotes': get_enum_bytes('ApplUsageQuotes', 'AutoSelect'),
            'OrderRoutingIndicator': get_enum_bytes('OrderRoutingIndicator', 'No'),
            'FIXEngineName': b'PyOES',
            'FIXEngineVersion': b'0.0.1',
            'FIXEngineVendor': b'None',
            'ApplicationSystemName': b'User',
            'ApplicationSystemVersion': b'0.0.1',
            'ApplicationSystemVendor': b'EUREX/XETRA member id',
        })
        logon_response = await self.recv('LogonResponseT')
        for user in users:
            user_login = await self.send('UserLoginRequestT', {
                'Username': user.id,
                'Password': user.password,
            })
            user_login_response = await self.recv('UserLoginResponseT')
        # ask for retransmits until there's nothing left to retransmit
        while True:
            logger.info("Requesting retransmit starting at %s", self.last_appl_msg_id)
            await self.send('RetransmitMEMessageRequestT', {
                'RefApplID': get_enum("APPLID", "SessionData"),
                'ApplBegMsgID': self.last_appl_msg_id,
                'ApplEndMsgID': b'\xff'*16,
            })
            retransmit_response = await self.recv("RetransmitMEMessageResponseT")
            logger.info("Receiving retransmit of %d messages ending at %s",
                        retransmit_response.ApplTotalMessageCount,
                        b"".join(retransmit_response.ApplEndMsgID))
            if retransmit_response.ApplTotalMessageCount == 0:
                break
            for _ in range(retransmit_response.ApplTotalMessageCount):
                msg = await self.recv()
                # it was a sequenced message
                assert extract_appl_header(msg)
                # TODO do something with it... like log state...
        nursery.start_soon(self._run)
        return self

    async def _run(self) -> None:
        while True:
            try:
                msg = await self.recv()
            except EOFError:
                break
            if hasattr(msg, 'ClOrdID'):
                self.cl_ord_ids[msg.ClOrdID].queue.put(msg)
            else:
                raise Exception("got unhandled", msg, ps(msg))

    def _allocate_cl_ord_id(self) -> ClOrdID:
        ret = ClOrdID(len(self.cl_ord_ids) + 1000)
        self.cl_ord_ids[ret.number] = ret
        return ret

    async def send_order(self, price: Decimal, quantity: int, side: Side, tif: TimeInForce) -> Order:
        cl_ord_id = self._allocate_cl_ord_id()
        new_order_single = await self.send('NewOrderSingleShortRequestT', {
            'Price': decimal_to_price(price),
            'OrderQty': quantity,
            'ClOrdID': cl_ord_id.number,
            'PartyIdInvestmentDecisionMaker': 1234, # an id for a human or tactic sending an order - like CME
            'ExecutingTrader': 1234,
            'MatchInstCrossID': 1234,
            'EnrichmentRuleID': 1234,
            'Side': get_enum('Side', side.value),
            # TODO actually, no recovery might be fine
            'ApplSeqIndicator': get_enum('ApplSeqIndicator', 'RecoveryRequired'),
            'PriceValidityCheckType': get_enum('PriceValidityCheckType', 'Mandatory'),
            'ValueCheckTypeValue': get_enum('ValueCheckTypeValue', 'Check'),
            'OrderAttributeLiquidityProvision': get_enum('OrderAttributeLiquidityProvision', 'N'),
            'TimeInForce': get_enum('TimeInForce', tif.value), 
        })
        order = Order(
            self,
            cl_ord_id,
            new_order_single,
            PersistentQueue(),
        )
        return order

    async def close(self) -> None:
        await self.buf.fd.handle.shutdown(SHUT.RDWR)
