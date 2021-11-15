from rsyscall.tests.trio_test_case import TrioTestCase
from rsyscall import local_thread
from rsyscall.stdlib import mkdtemp
from rsyscall.sys.socket import AF, SOCK
from rsyscall.sys.un import SockaddrUn
from order_entry_servers.eurex.server import Server
from order_entry_servers.eurex.client import Client, OrderCanceled, OrderFilled
from order_entry_servers.eurex.protocol import *
import logging

logger = logging.getLogger(__name__)

class Test(TrioTestCase):
    async def asyncSetUp(self) -> None:
        self.thread = local_thread
        self.dir = await mkdtemp(self.thread)
        addr = await self.thread.ptr(await SockaddrUn.from_path(self.thread, self.dir/"sock"))
        listening = await self.thread.make_afd(await self.thread.socket(AF.UNIX, SOCK.STREAM|SOCK.NONBLOCK))
        await listening.bind(addr)
        await listening.handle.listen(10)
        # TODO we should really be getting the address out... of the server?
        # and maybe we can make the listening socket inside there...
        # but then we'd want a bind_getsockname...
        # although bind_getsockname is awkward with SockaddrUn
        self.server = await Server.start(self.nursery, listening)
        connected = await self.thread.make_afd(await self.thread.socket(AF.UNIX, SOCK.STREAM|SOCK.NONBLOCK))
        await connected.connect(addr)
        self.client = await Client.connect(self.nursery, connected, [User(123, b"pass")])

    async def test_main(self) -> None:
        logger.info("Rejected/canceled without filling")
        order = await self.client.send_order(Decimal('50.1'), 100, Side.Buy, TimeInForce.Day)
        server_order = await self.server.orders.get()
        await server_order.accept(canceled=True)
        with self.assertRaises(OrderCanceled):
            await order.fills.get()

        logger.info("Full fill on accept")
        order = await self.client.send_order(Decimal('50.1'), 100, Side.Buy, TimeInForce.Day)
        server_order = await self.server.orders.get()
        await server_order.accept_fill(order.price, order.quantity)
        fill = await order.fills.get()
        self.assertEqual((fill.price, fill.quantity), (order.price, order.quantity))
        with self.assertRaises(OrderFilled):
            print(await order.fills.get())

        logger.info("Fill and unsolicited cancel")
        order = await self.client.send_order(Decimal('50.1'), 100, Side.Buy, TimeInForce.Day)
        server_order = await self.server.orders.get()
        await server_order.accept()
        await server_order.fill(order.price, order.quantity//2)
        await server_order.unsolicited_cancel()
        fill = await order.fills.get()
        with self.assertRaises(OrderCanceled):
            print(await order.fills.get())

        logger.info("Solicited cancel")
        order = await self.client.send_order(Decimal('50.1'), 100, Side.Buy, TimeInForce.Day)
        server_order = await self.server.orders.get()
        await server_order.accept()
        await order.cancel()
        with self.assertRaises(OrderCanceled):
            print(await order.fills.get())
