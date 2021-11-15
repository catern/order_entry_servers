from __future__ import annotations
import trio
import time
from order_entry_servers.eurex.protocol import *
from rsyscall import AsyncFileDescriptor
from rsyscall.epoller import AsyncReadBuffer

@dataclasses.dataclass
class Order:
    client: Client
    cl_ord_id: ClOrdID
    new_order_single: ffi.CData
    fills: PersistentQueue[ffi.CData]

    def __post_init__(self) -> None:
        dneio.reset(self._run())

    @property
    def price(self) -> Decimal:
        return price_to_decimal(self.new_order_single.Price)

    @property
    def quantity(self) -> int:
        return self.new_order_single.OrderQty

    async def _run(self) -> None:
        while True:
            response = await self.cl_ord_id.queue.get()
            if ffi.typeof(response) == ffi.typeof('OrderExecNotificationT'):
                self.fills.put(response)
            else:
                raise Exception(self, "got unhandled", msg, ps(msg))

@dataclasses.dataclass
class Client:
    buf: AsyncReadBuffer
    users: List[User]
    cl_ord_ids: Dict[int, ClOrdID]
    seq_num: int = 1

    async def send(self, msg_type: str, fields: Dict[str, Any]) -> ffi.CData:
        seq_num = self.seq_num
        self.seq_num += 1
        msg = to_in_struct(msg_type, {
            **fields,
            'RequestHeader': {'MsgSeqNum': seq_num},
        })
        await self.buf.fd.write_all_bytes(bytes(ffi.buffer(msg)))
        return msg

    async def recv(self, msg_type: str=None) -> ffi.CData:
        header = await self.buf.read_cffi('MessageHeaderOutCompT', remove=False)
        msg = copy_cast(tid_to_type[header.TemplateID], await self.buf.read_length(header.BodyLen))
        if msg_type:
            assert ffi.typeof(msg) == ffi.typeof(msg_type)
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
            print(user_login_response, ps(user_login_response))
        # TODO now ask for retransmits of... everything
        nursery.start_soon(self._run)
        return self

    async def _run(self) -> None:
        while True:
            msg = await self.recv()
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
            'RequestHeader': {'MsgSeqNum': 2},
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
