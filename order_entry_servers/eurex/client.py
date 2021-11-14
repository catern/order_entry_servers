from __future__ import annotations
import trio
import time
from order_entry_servers.eurex.protocol import *
from rsyscall import AsyncFileDescriptor
from rsyscall.epoller import AsyncReadBuffer

@dataclasses.dataclass
class Client:
    buf: AsyncReadBuffer
    users: List[User]
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
        data = await self.buf.read_length(ffi.sizeof('MessageHeaderOutCompT'))
        header = ffi.cast('MessageHeaderOutCompT*', ffi.from_buffer(data))
        data += await self.buf.read_length(header.BodyLen - len(data))
        msg = ffi.cast(tid_to_type[header.TemplateID] + '*', ffi.from_buffer(data))[0]
        if msg_type:
            assert ffi.typeof(msg) == ffi.typeof(msg_type)
        return msg

    @classmethod
    async def connect(cls, nursery: trio.Nursery, sock: AsyncFileDescriptor, users: List[User]) -> Client:
        self = Client(
            AsyncReadBuffer(sock),
            users=users,
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
        # now ask for retransmits of... everything
        return self

    async def send_order(self, price: Decimal, quantity: int, side: Side, tif: TimeInForce) -> Order:
        cl_ord_id = 1234 # TODO should allocate this...
        order = to_in_struct('NewOrderSingleShortRequestT', {
            'RequestHeader': {'MsgSeqNum': 2},
            'Price': price,
            'OrderQty': quantity,
            'ClOrdID': cl_ord_id,
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
        # print(ps(order))
