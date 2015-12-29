from __future__ import absolute_import, print_function

import unittest

import logging
import bokeh.document as document
from bokeh.application import Application
from bokeh.application.handlers import FunctionHandler
from bokeh.client import pull_session, push_session, ClientSession
from bokeh.document import ModelChangedEvent, TitleChangedEvent
from bokeh.server.server import Server
from bokeh.server.session import ServerSession
from bokeh.model import Model
from bokeh.resources import websocket_url_for_server_url
from bokeh.core.properties import Int, Instance, Dict, String, Any, DistanceSpec, AngleSpec
from tornado.ioloop import IOLoop, PeriodicCallback, _Timeout
from tornado import gen

class AnotherModelInTestClientServer(Model):
    bar = Int(1)

class SomeModelInTestClientServer(Model):
    foo = Int(2)
    child = Instance(Model)


class DictModel(Model):
    values = Dict(String, Any)

class UnitsSpecModel(Model):
    distance = DistanceSpec(42)
    angle = AngleSpec(0)

logging.basicConfig(level=logging.DEBUG)

# just for testing
def url(server, prefix=""):
    return "http://localhost:" + str(server._port) + prefix + "/"

def ws_url(server, prefix=""):
    return "ws://localhost:" + str(server._port) + prefix + "/ws"

# lets us use a current IOLoop with "with"
# and ensures the server unlistens
class ManagedServerLoop(object):
    def __init__(self, application, **server_kwargs):
        loop = IOLoop()
        loop.make_current()
        server_kwargs['io_loop'] = loop
        self._server = Server(application, **server_kwargs)
    def __exit__(self, type, value, traceback):
        self._server.unlisten()
        self._server.io_loop.close()
    def __enter__(self):
        return self._server
    @property
    def io_loop(self):
        return self.s_server.io_loop

class TestClientServer(unittest.TestCase):

    def test_minimal_connect_and_disconnect(self):
        application = Application()
        with ManagedServerLoop(application) as server:
            # we don't have to start the server because it
            # uses the same main loop as the client, so
            # if we start either one it starts both
            session = ClientSession(session_id='test_minimal_connect_and_disconnect',
                                    io_loop = server.io_loop,
                                    websocket_url = ws_url(server))
            session.connect()
            assert session.connected

    def test_disconnect_on_error(self):
        application = Application()
        with ManagedServerLoop(application) as server:
            session = ClientSession(session_id='test_disconnect_on_error',
                                    websocket_url=ws_url(server),
                                    io_loop = server.io_loop)
            session.connect()
            assert session.connected
            # send a bogus message using private fields
            session._connection._socket.write_message(b"xx", binary=True)
            # connection should now close on the server side
            # and the client loop should end
            session.loop_until_closed()
            assert not session.connected
            session.close()
            session.loop_until_closed()
            assert not session.connected

    def test_connect_with_prefix(self):
        application = Application()
        with ManagedServerLoop(application, prefix="foo") as server:
            # we don't have to start the server because it
            # uses the same main loop as the client, so
            # if we start either one it starts both
            session = ClientSession(io_loop = server.io_loop,
                                    websocket_url = ws_url(server, "/foo"))
            session.connect()
            assert session.connected
            session.close()
            session.loop_until_closed()

            session = ClientSession(io_loop = server.io_loop,
                                    websocket_url = ws_url(server))
            session.connect()
            assert not session.connected
            session.close()
            session.loop_until_closed()

    def test_host_whitelist_success(self):
        application = Application()

        # succeed no host value with defaults
        with ManagedServerLoop(application, host=None) as server:
            session = ClientSession(websocket_url=ws_url(server), io_loop = server.io_loop)
            session.connect()
            assert session.connected
            session.close()
            session.loop_until_closed()

        # succeed no host value with port
        with ManagedServerLoop(application, port=8080, host=None) as server:
            session = ClientSession(websocket_url=ws_url(server), io_loop = server.io_loop)
            session.connect()
            assert session.connected
            session.close()
            session.loop_until_closed()

        # succeed matching host value
        with ManagedServerLoop(application, port=8080, host=["localhost:8080"]) as server:
            session = ClientSession(websocket_url=ws_url(server), io_loop = server.io_loop)
            session.connect()
            assert session.connected
            session.close()
            session.loop_until_closed()

        # succeed matching host value one of multiple
        with ManagedServerLoop(application, port=8080, host=["bad_host", "localhost:8080"]) as server:
            session = ClientSession(websocket_url=ws_url(server), io_loop = server.io_loop)
            session.connect()
            assert session.connected
            session.close()
            session.loop_until_closed()

    def test_host_whitelist_failure(self):
        application = Application()

        # failure bad host
        with ManagedServerLoop(application, host=["bad_host"]) as server:
            session = ClientSession(websocket_url=ws_url(server), io_loop = server.io_loop)
            session.connect()
            assert not session.connected
            session.close()
            session.loop_until_closed()

        with ManagedServerLoop(application, host=["bad_host:5006"]) as server:
            session = ClientSession(websocket_url=ws_url(server), io_loop = server.io_loop)
            session.connect()
            assert not session.connected
            session.close()
            session.loop_until_closed()

        # failure good host, bad port
        with ManagedServerLoop(application, host=["localhost:80"]) as server:
            session = ClientSession(websocket_url=ws_url(server), io_loop = server.io_loop)
            session.connect()
            assert not session.connected
            session.close()
            session.loop_until_closed()

        # failure good host, bad default port
        with ManagedServerLoop(application, host=["localhost"]) as server:
            session = ClientSession(websocket_url=ws_url(server), io_loop = server.io_loop)
            session.connect()
            assert not session.connected
            session.close()
            session.loop_until_closed()

        # failure with custom port
        with ManagedServerLoop(application, port=8080, host=["localhost:8081"]) as server:
            session = ClientSession(websocket_url=ws_url(server), io_loop = server.io_loop)
            session.connect()
            assert not session.connected
            session.close()
            session.loop_until_closed()

    def test_push_document(self):
        application = Application()
        with ManagedServerLoop(application) as server:
            doc = document.Document()
            doc.add_root(AnotherModelInTestClientServer(bar=43))
            doc.add_root(SomeModelInTestClientServer(foo=42))

            client_session = push_session(doc,
                                          session_id='test_push_document',
                                          url=url(server),
                                          io_loop=server.io_loop)

            assert client_session.document == doc
            assert len(client_session.document.roots) == 2

            server_session = server.get_session('/', client_session.id)

            assert len(server_session.document.roots) == 2
            results = {}
            for r in server_session.document.roots:
                if hasattr(r, 'foo'):
                    results['foo'] = r.foo
                if hasattr(r, 'bar'):
                    results['bar'] = r.bar
            assert results['foo'] == 42
            assert results['bar'] == 43

            client_session.close()
            client_session.loop_until_closed()
            assert not client_session.connected

    def test_pull_document(self):
        application = Application()
        def add_roots(doc):
            doc.add_root(AnotherModelInTestClientServer(bar=43))
            doc.add_root(SomeModelInTestClientServer(foo=42))
        handler = FunctionHandler(add_roots)
        application.add(handler)

        with ManagedServerLoop(application) as server:
            client_session = pull_session(session_id='test_pull_document',
                                          url=url(server),
                                          io_loop=server.io_loop)
            assert len(client_session.document.roots) == 2

            server_session = server.get_session('/', client_session.id)
            assert len(server_session.document.roots) == 2

            results = {}
            for r in server_session.document.roots:
                if hasattr(r, 'foo'):
                    results['foo'] = r.foo
                if hasattr(r, 'bar'):
                    results['bar'] = r.bar
            assert results['foo'] == 42
            assert results['bar'] == 43

            client_session.close()
            client_session.loop_until_closed()
            assert not client_session.connected

    def test_request_server_info(self):
        application = Application()
        with ManagedServerLoop(application) as server:
            session = ClientSession(session_id='test_request_server_info',
                                    websocket_url=ws_url(server),
                                    io_loop=server.io_loop)
            session.connect()
            assert session.connected
            assert session.document is None

            info = session.request_server_info()

            from bokeh import __version__

            assert info['version_info']['bokeh'] == __version__
            assert info['version_info']['server'] == __version__

            session.close()
            session.loop_until_closed()
            assert not session.connected

    def test_ping(self):
        application = Application()
        with ManagedServerLoop(application, keep_alive_milliseconds=0) as server:
            session = ClientSession(session_id='test_ping',
                                    websocket_url=ws_url(server),
                                    io_loop=server.io_loop)
            session.connect()
            assert session.connected
            assert session.document is None

            connection = next(iter(server._tornado._clients))
            expected_pong = connection._ping_count
            server._tornado.keep_alive() # send ping
            session.force_roundtrip()

            self.assertEqual(expected_pong, connection._socket.latest_pong)

            # check that each ping increments by 1
            server._tornado.keep_alive()
            session.force_roundtrip()

            self.assertEqual(expected_pong + 1, connection._socket.latest_pong)

            session.close()
            session.loop_until_closed()
            assert not session.connected

    def test_client_changes_go_to_server(self):
        application = Application()
        with ManagedServerLoop(application) as server:
            doc = document.Document()
            client_root = SomeModelInTestClientServer(foo=42)

            client_session = push_session(doc, session_id='test_client_changes_go_to_server',
                                          url=url(server),
                                          io_loop=server.io_loop)
            server_session = server.get_session('/', client_session.id)

            assert len(server_session.document.roots) == 0

            doc.add_root(client_root)
            client_session.force_roundtrip() # be sure events have been handled on server

            assert len(server_session.document.roots) == 1
            server_root = next(iter(server_session.document.roots))

            assert client_root.foo == 42
            assert server_root.foo == 42

            # Now try setting title
            assert server_session.document.title == document.DEFAULT_TITLE
            doc.title = "Client Title"
            client_session.force_roundtrip() # be sure events have been handled on server

            assert server_session.document.title == "Client Title"

            # Now modify an attribute on a client model
            client_root.foo = 57

            # there is no great way to block until the server
            # has applied changes, since patches are sent
            # asynchronously. We use internal _loop_until API.
            def server_change_made():
                return server_root.foo == 57
            client_session._connection._loop_until(server_change_made)
            assert server_root.foo == 57

            doc.remove_root(client_root)
            client_session.force_roundtrip() # be sure events have been handled on server
            assert len(server_session.document.roots) == 0

            client_session.close()
            client_session.loop_until_closed()
            assert not client_session.connected

    def test_server_changes_go_to_client(self):
        application = Application()
        with ManagedServerLoop(application) as server:
            doc = document.Document()

            client_session = push_session(doc,
                                          session_id='test_server_changes_go_to_client',
                                          url=url(server),
                                          io_loop=server.io_loop)
            server_session = server.get_session('/', client_session.id)

            assert len(client_session.document.roots) == 0
            server_root = SomeModelInTestClientServer(foo=42)

            def do_add_server_root():
                server_session.document.add_root(server_root)
            server_session.with_document_locked(do_add_server_root)

            def client_has_root():
                return len(doc.roots) > 0
            client_session._connection._loop_until(client_has_root)
            client_root = next(iter(client_session.document.roots))

            assert client_root.foo == 42
            assert server_root.foo == 42

            # Now try setting title on server side
            def do_set_server_title():
                server_session.document.title = "Server Title"
            server_session.with_document_locked(do_set_server_title)

            def client_title_set():
                return client_session.document.title != document.DEFAULT_TITLE
            client_session._connection._loop_until(client_title_set)

            assert client_session.document.title == "Server Title"

            # Now modify a model within the server document
            def do_set_property_on_server():
                server_root.foo = 57
            server_session.with_document_locked(do_set_property_on_server)

            # there is no great way to block until the server
            # has applied changes, since patches are sent
            # asynchronously. We use internal _loop_until API.
            def client_change_made():
                return client_root.foo == 57
            client_session._connection._loop_until(client_change_made)
            assert client_root.foo == 57

            def do_remove_server_root():
                server_session.document.remove_root(server_root)
            server_session.with_document_locked(do_remove_server_root)

            def client_lacks_root():
                return len(doc.roots) == 0
            client_session._connection._loop_until(client_lacks_root)
            assert len(client_session.document.roots) == 0

            client_session.close()
            client_session.loop_until_closed()
            assert not client_session.connected

    def test_io_push_to_server(self):
        from bokeh.io import output_server, push, curdoc, reset_output
        application = Application()
        with ManagedServerLoop(application) as server:
            reset_output()
            doc = curdoc()
            doc.clear()

            client_root = SomeModelInTestClientServer(foo=42)

            session_id = 'test_io_push_to_server'
            output_server(session_id=session_id,
                          url=("http://localhost:%d/" % server.port))

            doc.add_root(client_root)
            push(io_loop=server.io_loop)

            server_session = server.get_session('/', session_id)

            print(repr(server_session.document.roots))

            assert len(server_session.document.roots) == 1
            server_root = next(iter(server_session.document.roots))

            assert client_root.foo == 42
            assert server_root.foo == 42

            # Now modify the client document and push
            client_root.foo = 57
            push(io_loop=server.io_loop)
            server_root = next(iter(server_session.document.roots))
            assert server_root.foo == 57

            # Remove a root and push
            doc.remove_root(client_root)
            push(io_loop=server.io_loop)
            assert len(server_session.document.roots) == 0

            # Clean up global IO state
            reset_output()

    def test_session_periodic_callback(self):
        application = Application()
        with ManagedServerLoop(application) as server:
            doc = document.Document()

            client_session = ClientSession(session_id='test_client_session_callback',
                                          websocket_url=ws_url(server),
                                          io_loop=server.io_loop)
            server_session = ServerSession(session_id='test_server_session_callback',
                                           document=doc, io_loop=server.io_loop)
            client_session._attach_document(doc)

            assert len(server_session._callbacks) == 0
            assert len(client_session._callbacks) == 0

            def cb(): pass
            callback = doc.add_periodic_callback(cb, 1, 'abc')
            server_session2 = ServerSession('test_server_session_callback',
                                            doc, server.io_loop)

            assert server_session2._callbacks
            assert len(server_session._callbacks) == 1
            assert len(client_session._callbacks) == 1

            started_callbacks = []
            for ss in [server_session, server_session2]:
                iocb = ss._callbacks[callback.id]
                assert iocb._period == 1
                assert iocb._loop == server.io_loop
                assert iocb._started
                assert not iocb._stopped
                started_callbacks.append(iocb)

            for ss in [client_session]:
                iocb = ss._callbacks[callback.id]
                assert iocb._period == 1
                assert iocb._loop == server.io_loop
                assert iocb._started
                assert not iocb._stopped
                started_callbacks.append(iocb)

            callback = doc.remove_periodic_callback(cb)
            assert len(server_session._callbacks) == 0
            assert len(client_session._callbacks) == 0
            assert len(server_session._callbacks) == 0

            for iocb in started_callbacks:
                assert iocb._stopped # server

    def test_session_timeout_callback(self):
        application = Application()
        with ManagedServerLoop(application) as server:
            doc = document.Document()

            client_session = ClientSession(session_id='test_client_session_callback',
                                          websocket_url=ws_url(server),
                                          io_loop=server.io_loop)
            server_session = ServerSession(session_id='test_server_session_callback',
                                           document=doc, io_loop=server.io_loop)
            client_session._attach_document(doc)

            assert len(server_session._callbacks) == 0
            assert len(client_session._callbacks) == 0

            def cb(): pass

            x = server.io_loop.time()
            callback = doc.add_timeout_callback(cb, 10, 'abc')
            server_session2 = ServerSession('test_server_session_callback',
                                            doc, server.io_loop)

            assert server_session2._callbacks
            assert len(server_session._callbacks) == 1
            assert len(client_session._callbacks) == 1

            started_callbacks = []
            for ss in [server_session, client_session, server_session2]:
                iocb = ss._callbacks[callback.id]
                assert isinstance(iocb, _Timeout)

                # check that the callback deadline is 10
                # milliseconds later from when we called
                # add_timeout_callback (using int to avoid ms
                # differences between the x definition and the
                # call)
                assert abs(int(iocb.deadline) - int(x + 10/1000.0)) < 1e6
                started_callbacks.append(iocb)

            callback = doc.remove_timeout_callback(cb)
            assert len(server_session._callbacks) == 0
            assert len(client_session._callbacks) == 0
            assert len(server_session._callbacks) == 0

    @gen.coroutine
    def async_value(self, value):
        yield gen.moment # this ensures we actually return to the loop
        raise gen.Return(value)

    def test_client_session_timeout_async(self):
        application = Application()
        with ManagedServerLoop(application) as server:
            doc = document.Document()

            client_session = push_session(doc,
                                          session_id='test_client_session_timeout_async',
                                          url=url(server),
                                          io_loop=server.io_loop)

            result = DictModel()
            doc.add_root(result)

            @gen.coroutine
            def cb():
                result.values['a'] = 0
                result.values['b'] = yield self.async_value(1)
                result.values['c'] = yield self.async_value(2)
                result.values['d'] = yield self.async_value(3)
                result.values['e'] = yield self.async_value(4)
                client_session.close()
                raise gen.Return(5)

            callback = doc.add_timeout_callback(cb, 10)

            client_session.loop_until_closed()

            doc.remove_timeout_callback(cb)

            self.assertDictEqual(dict(a=0, b=1, c=2, d=3, e=4), result.values)

    def test_server_session_timeout_async(self):
        application = Application()
        with ManagedServerLoop(application) as server:
            doc = document.Document()
            doc.add_root(DictModel())

            client_session = push_session(doc,
                                          session_id='test_server_session_timeout_async',
                                          url=url(server),
                                          io_loop=server.io_loop)
            server_session = server.get_session('/', client_session.id)

            result = next(iter(server_session.document.roots))

            @gen.coroutine
            def cb():
                # we're testing that we can modify the doc and be
                # "inside" the document lock
                result.values['a'] = 0
                result.values['b'] = yield self.async_value(1)
                result.values['c'] = yield self.async_value(2)
                result.values['d'] = yield self.async_value(3)
                result.values['e'] = yield self.async_value(4)
                client_session.close()
                raise gen.Return(5)

            callback = server_session.document.add_timeout_callback(cb, 10)

            client_session.loop_until_closed()

            server_session.document.remove_timeout_callback(cb)

            self.assertDictEqual(dict(a=0, b=1, c=2, d=3, e=4), result.values)

    def test_client_session_periodic_async(self):
        application = Application()
        with ManagedServerLoop(application) as server:
            doc = document.Document()

            client_session = push_session(doc,
                                          session_id='test_client_session_periodic_async',
                                          url=url(server),
                                          io_loop=server.io_loop)

            result = DictModel()
            doc.add_root(result)

            @gen.coroutine
            def cb():
                result.values['a'] = 0
                result.values['b'] = yield self.async_value(1)
                result.values['c'] = yield self.async_value(2)
                result.values['d'] = yield self.async_value(3)
                result.values['e'] = yield self.async_value(4)
                client_session.close()
                raise gen.Return(5)

            callback = doc.add_periodic_callback(cb, 10)

            client_session.loop_until_closed()

            doc.remove_periodic_callback(cb)

            self.assertDictEqual(dict(a=0, b=1, c=2, d=3, e=4), result.values)

    def test_server_session_periodic_async(self):
        application = Application()
        with ManagedServerLoop(application) as server:
            doc = document.Document()
            doc.add_root(DictModel())

            client_session = push_session(doc,
                                          session_id='test_server_session_periodic_async',
                                          url=url(server),
                                          io_loop=server.io_loop)
            server_session = server.get_session('/', client_session.id)

            result = next(iter(server_session.document.roots))

            @gen.coroutine
            def cb():
                # we're testing that we can modify the doc and be
                # "inside" the document lock
                result.values['a'] = 0
                result.values['b'] = yield self.async_value(1)
                result.values['c'] = yield self.async_value(2)
                result.values['d'] = yield self.async_value(3)
                result.values['e'] = yield self.async_value(4)
                client_session.close()
                raise gen.Return(5)

            callback = server_session.document.add_periodic_callback(cb, 10)

            client_session.loop_until_closed()

            server_session.document.remove_periodic_callback(cb)

            self.assertDictEqual(dict(a=0, b=1, c=2, d=3, e=4), result.values)

    def test_lots_of_concurrent_messages(self):
        application = Application()
        def setup_stuff(doc):
            m1 = AnotherModelInTestClientServer(bar=43, name='m1')
            m2 = SomeModelInTestClientServer(foo=42, name='m2')
            m3 = SomeModelInTestClientServer(foo=68, name='m3')
            doc.add_root(m1)
            doc.add_root(m2)
            doc.add_root(m3)
            def timeout1():
                m1.bar += 1
            doc.add_timeout_callback(timeout1, 1)
            def timeout2():
                m2.foo +=1
            doc.add_timeout_callback(timeout2, 3)
            def periodic1():
                m1.bar += 1
                doc.remove_timeout_callback(timeout1)
                doc.add_timeout_callback(timeout1, m1.bar % 7)
            doc.add_periodic_callback(periodic1, 3)
            def periodic2():
                m2.foo += 1
                doc.remove_timeout_callback(timeout2)
                doc.add_timeout_callback(timeout2, m2.foo % 7)
            doc.add_periodic_callback(periodic2, 1)

            def server_on_change(event):
                if isinstance(event, ModelChangedEvent) and event.model is m3:
                    return
                m3.foo += 1

            doc.on_change(server_on_change)

        handler = FunctionHandler(setup_stuff)
        application.add(handler)

        # keep_alive_milliseconds=1 sends pings as fast as the OS will let us
        with ManagedServerLoop(application, keep_alive_milliseconds=1) as server:
            session = pull_session(session_id='test_lots_of_concurrent_messages',
                                   url=url(server),
                                   io_loop=server.io_loop)
            assert session.connected

            server_session = server.get_session('/', session.id)

            def client_timeout():
                m = session.document.roots[0]
                m.name = m.name[::-1]
            session.document.add_timeout_callback(client_timeout, 3)

            def client_periodic():
                m = session.document.roots[1]
                m.name = m.name[::-1]
                session.document.remove_timeout_callback(client_timeout)
                session.document.add_timeout_callback(client_timeout, 3)

            session.document.add_periodic_callback(client_periodic, 1)

            result = {}
            def end_test():
                result['connected'] = session.connected
                result['server_connection_count'] = server_session.connection_count
                result['server_close_code'] = next(iter(server._tornado._clients))._socket.close_code
                result['doc'] = session.document.to_json()
                session.close()

            # making this longer is more likely to trigger bugs, but it also
            # makes the test take however much time you put here
            session.document.add_timeout_callback(end_test, 250)

            def client_on_change(event):
                if not isinstance(event, TitleChangedEvent):
                    session.document.title = session.document.title[::-1]

            session.document.on_change(client_on_change)

            session.loop_until_closed()

            assert not session.connected

            # we should have still been connected at the end,
            # if we didn't have any crazy protocol errors
            assert 'connected' in result
            assert result['connected']

            # server should also still have been connected
            assert result['server_connection_count'] == 1
            assert result['server_close_code'] is None

# This isn't in the unittest.TestCase because per-test fixtures
# don't work there (see note at bottom of https://pytest.org/latest/unittest.html#unittest-testcase)
def test_client_changes_do_not_boomerang(monkeypatch):
    application = Application()
    with ManagedServerLoop(application) as server:
        doc = document.Document()
        client_root = SomeModelInTestClientServer(foo=42)
        doc.add_root(client_root)

        client_session = push_session(doc,
                                      session_id='test_client_changes_do_not_boomerang',
                                      url=url(server),
                                      io_loop=server.io_loop)
        server_session = server.get_session('/', client_session.id)

        assert len(server_session.document.roots) == 1
        server_root = next(iter(server_session.document.roots))

        assert client_root.foo == 42
        assert server_root.foo == 42

        got_angry = {}
        got_angry['result'] = None
        # trap any boomerang
        def get_angry(message):
            got_angry['result'] = message
        monkeypatch.setattr(client_session, '_handle_patch', get_angry)

        # Now modify the client document
        client_root.foo = 57

        # wait until server side change made ... but we may not have the
        # boomerang yet
        def server_change_made():
            return server_root.foo == 57
        client_session._connection._loop_until(server_change_made)
        assert server_root.foo == 57

        # force a round trip to be sure we get the boomerang if we're going to
        client_session.force_roundtrip()

        assert got_angry['result'] is None

        client_session.close()
        client_session.loop_until_closed()
        assert not client_session.connected
        server.unlisten() # clean up so next test can run

# This isn't in the unittest.TestCase because per-test fixtures
# don't work there (see note at bottom of https://pytest.org/latest/unittest.html#unittest-testcase)
def test_server_changes_do_not_boomerang(monkeypatch):
    application = Application()
    with ManagedServerLoop(application) as server:
        doc = document.Document()
        client_root = SomeModelInTestClientServer(foo=42)
        doc.add_root(client_root)

        client_session = push_session(doc,
                                      session_id='test_server_changes_do_not_boomerang',
                                      url=url(server),
                                      io_loop=server.io_loop)
        server_session = server.get_session('/', client_session.id)

        assert len(server_session.document.roots) == 1
        server_root = next(iter(server_session.document.roots))

        assert client_root.foo == 42
        assert server_root.foo == 42

        got_angry = {}
        got_angry['result'] = None
        # trap any boomerang
        def get_angry(message, connection):
            got_angry['result'] = message
        monkeypatch.setattr(server_session, '_handle_patch', get_angry)

        # Now modify the server document
        def do_set_foo_property():
            server_root.foo = 57
        server_session.with_document_locked(do_set_foo_property)

        # there is no great way to block until the server
        # has applied changes, since patches are sent
        # asynchronously. We use internal _loop_until API.
        def client_change_made():
            return client_root.foo == 57
        client_session._connection._loop_until(client_change_made)
        assert client_root.foo == 57

        # force a round trip to be sure we get the boomerang if we're going to
        client_session.force_roundtrip()

        assert got_angry['result'] is None

        client_session.close()
        client_session.loop_until_closed()
        assert not client_session.connected

# this test is because we do the funky serializable_value
# tricks with the units specs
def test_unit_spec_changes_do_not_boomerang(monkeypatch):
    application = Application()
    with ManagedServerLoop(application) as server:
        doc = document.Document()
        client_root = UnitsSpecModel()
        doc.add_root(client_root)

        client_session = push_session(doc,
                                      session_id='test_unit_spec_changes_do_not_boomerang',
                                      url=url(server),
                                      io_loop=server.io_loop)
        server_session = server.get_session('/', client_session.id)

        assert len(server_session.document.roots) == 1
        server_root = next(iter(server_session.document.roots))

        assert client_root.distance == 42
        assert server_root.angle == 0

        def change_to(new_distance, new_angle):
            got_angry = {}
            got_angry['result'] = None
            # trap any boomerang
            def get_angry(message):
                got_angry['result'] = message
            monkeypatch.setattr(client_session, '_handle_patch', get_angry)

            server_previous_distance = server_root.distance
            server_previous_angle = server_root.angle

            # Now modify the client document
            client_root.distance = new_distance
            client_root.angle = new_angle

            # wait until server side change made ... but we may not have the
            # boomerang yet
            def server_change_made():
                return server_root.distance != server_previous_distance and \
                    server_root.angle != server_previous_angle
            client_session._connection._loop_until(server_change_made)

            # force a round trip to be sure we get the boomerang if we're going to
            client_session.force_roundtrip()

            assert got_angry['result'] is None

        change_to(57, 1)
        change_to({ 'value' : 58 }, { 'value' : 2 })
        change_to({ 'field' : 'foo' }, { 'field' : 'bar' })
        change_to({ 'value' : 59, 'units' : 'screen' }, { 'value' : 30, 'units' : 'deg' })

        client_session.close()
        client_session.loop_until_closed()
        assert not client_session.connected
        server.unlisten() # clean up so next test can run
