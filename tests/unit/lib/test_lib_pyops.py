import pytest
import os
import tempfile
import zmq
import msgpack
import json
import signal
from unittest.mock import Mock, patch, MagicMock, ANY

@pytest.fixture
def mock_web_bind():
    return "127.0.0.1:8000"

@pytest.fixture
def mock_control_request(mock_web_bind):
    from app.lib import pyops
    with patch('requests.get') as mock_get, \
         patch('requests.delete') as mock_del, \
         patch('requests.post') as mock_post, \
         patch('requests.put') as mock_put:
        yield pyops.ControlRequest("test_user", mock_web_bind)
        # Cleanup not needed as fixtures

class TestControlRequest:
    def test_handle_response_success(self, mock_control_request):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b'{"key": "value"}'
        mock_response.reason = "OK"
        result = mock_control_request.handle_response("GET /test", mock_response)
        assert result == {"key": "value"}

    def test_handle_response_404(self, mock_control_request):
        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.content = b''
        mock_response.reason = "Not Found"
        with pytest.raises(Exception, match="GET /test: 404 Not Found"):
            mock_control_request.handle_response("GET /test", mock_response)

    def test_handle_response_error(self, mock_control_request):
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.content = b'{"error": "server error"}'
        mock_response.reason = "Internal Server Error"
        with pytest.raises(Exception, match="GET /test: 500 Internal Server Error"):
            mock_control_request.handle_response("GET /test", mock_response)

    @patch('requests.get')
    def test_get_success(self, mock_get, mock_control_request):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b'{"data": "value"}'
        mock_get.return_value = mock_response
        result = mock_control_request.get("test/path", params={"p": "v"})
        assert result == {"data": "value"}
        mock_get.assert_called_once_with("http://127.0.0.1:8000/test/path", params={"p": "v"}, headers={"X-User": "test_user"})

    @patch('requests.delete')
    def test_delete_error(self, mock_del, mock_control_request):
        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.reason = "Not Found"
        mock_del.return_value = mock_response
        with pytest.raises(Exception):
            mock_control_request.delete("test/path")
        mock_del.assert_called_once_with("http://127.0.0.1:8000/test/path", params={}, headers={"X-User": "test_user"})

    @patch('requests.post')
    def test_post_success(self, mock_post, mock_control_request):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.content = b'{"posted": true}'
        mock_post.return_value = mock_response
        result = mock_control_request.post("test/path", json={"data": "value"})
        assert result == {"posted": True}
        mock_post.assert_called_once_with("http://127.0.0.1:8000/test/path", headers={"X-User": "test_user"}, json={"data": "value"})

    @patch('requests.put')
    def test_put_error(self, mock_put, mock_control_request):
        mock_response = Mock()
        mock_response.status_code = 409
        mock_response.content = b'{"conflict": true}'
        mock_response.reason = "Conflict"
        mock_put.return_value = mock_response
        with pytest.raises(Exception, match=r"PUT test/path: 409 Conflict"):
            mock_control_request.put("test/path", json={"data": "value"})
        mock_put.assert_called_once_with("http://127.0.0.1:8000/test/path", headers={"X-User": "test_user"}, json={"data": "value"})

class TestJobControl:
    def test_list(self, mock_control_request):
        from app.lib import pyops
        mock_rq = Mock()
        mock_rq.get.return_value = {"jobs": []}
        job_control = pyops.JobControl(mock_rq)
        result = job_control.list()
        assert result == {"jobs": []}
        mock_rq.get.assert_called_once_with("scriptlet/job/list")

    def test_create(self, mock_control_request):
        from app.lib import pyops
        mock_rq = Mock()
        mock_rq.put.return_value = {"job": {"id": "123"}}
        # No parent kind -> child defaults to "task".
        job_control = pyops.JobControl(mock_rq)
        result = job_control.create("test_set", "test_name", drop_done=True, param="param", debug=True)
        assert result == {"id": "123"}
        mock_rq.put.assert_called_once_with("scriptlet/job/item", headers={}, json={
            "set": "test_set",
            "name": "test_name",
            "drop_done": True,
            "max_retries": 0,
            "param": "param",
            "kind": "task",
            "debug": True
        })

    def test_create_with_user(self, mock_control_request):
        from app.lib import pyops
        mock_rq = Mock()
        mock_rq.put.return_value = {"job": {"id": "456"}}
        job_control = pyops.JobControl(mock_rq)
        result = job_control.create("test_set", "test_name", drop_done=False, param="user_param", debug=False, user="specific_user")
        assert result == {"id": "456"}
        mock_rq.put.assert_called_once_with("scriptlet/job/item", headers={"X-User": "specific_user"}, json={
            "set": "test_set",
            "name": "test_name",
            "drop_done": False,
            "max_retries": 0,
            "param": "user_param",
            "kind": "task",
            "debug": False
        })

    def test_create_inherits_draft_from_working_tree_parent(self, mock_control_request):
        """A job spawned from a working-tree parent (draft/debug/test/
        testdebug) defaults the child to `draft` so it runs the same
        uncommitted code the parent is iterating on — not committed HEAD."""
        from app.lib import pyops
        for parent_kind in ("draft", "debug", "test", "testdebug"):
            mock_rq = Mock()
            mock_rq.put.return_value = {"job": "jid"}
            job_control = pyops.JobControl(mock_rq, parent_kind)
            job_control.create("s", "n", param="p")
            assert mock_rq.put.call_args.kwargs["json"]["kind"] == "draft", \
                f"parent kind {parent_kind!r} must yield child kind 'draft'"

    def test_create_inherits_task_from_committed_parent(self, mock_control_request):
        """task/service parents (which run committed code) spawn `task`."""
        from app.lib import pyops
        for parent_kind in ("task", "service", None):
            mock_rq = Mock()
            mock_rq.put.return_value = {"job": "jid"}
            job_control = pyops.JobControl(mock_rq, parent_kind)
            job_control.create("s", "n", param="p")
            assert mock_rq.put.call_args.kwargs["json"]["kind"] == "task"

    def test_create_explicit_kind_overrides_inheritance(self, mock_control_request):
        """An explicit kind= argument always wins over parent inheritance."""
        from app.lib import pyops
        mock_rq = Mock()
        mock_rq.put.return_value = {"job": "jid"}
        # Parent is draft (would default to draft) but caller forces task.
        job_control = pyops.JobControl(mock_rq, "draft")
        job_control.create("s", "n", param="p", kind="task")
        assert mock_rq.put.call_args.kwargs["json"]["kind"] == "task"

    def test_get(self, mock_control_request):
        from app.lib import pyops
        mock_rq = Mock()
        mock_rq.get.return_value = {"job": {"state": "running"}}
        job_control = pyops.JobControl(mock_rq)
        result = job_control.get("123")
        assert result == {"job": {"state": "running"}}
        mock_rq.get.assert_called_once_with("scriptlet/job/item", params={"id": "123"})

    def test_delete(self, mock_control_request):
        from app.lib import pyops
        mock_rq = Mock()
        mock_rq.delete.return_value = {"deleted": True}
        job_control = pyops.JobControl(mock_rq)
        result = job_control.delete("123")
        assert result == {"deleted": True}
        mock_rq.delete.assert_called_once_with("scriptlet/job/item", params={"id": "123"})

    def test_cancel(self, mock_control_request):
        from app.lib import pyops
        mock_rq = Mock()
        mock_rq.get.return_value = {"cancelled": True}
        job_control = pyops.JobControl(mock_rq)
        result = job_control.cancel("123")
        assert result == {"cancelled": True}
        mock_rq.get.assert_called_once_with("scriptlet/job/cancel", params={"id": "123"})

    def test_download(self, mock_control_request):
        from app.lib import pyops
        mock_rq = Mock()
        mock_rq.get.return_value = {"download": "url"}
        job_control = pyops.JobControl(mock_rq)
        result = job_control.download("123")
        assert result == {"download": "url"}
        mock_rq.get.assert_called_once_with("scriptlet/job/download", params={"id": "123"})

class TestScheduleControl:
    def test_list(self, mock_control_request):
        from app.lib import pyops
        mock_rq = Mock()
        mock_rq.get.return_value = {"schedules": []}
        schedule_control = pyops.ScheduleControl(mock_rq)
        result = schedule_control.list()
        assert result == {"schedules": []}
        mock_rq.get.assert_called_once_with("scriptlet/schedule/list")

    def test_create(self, mock_control_request):
        from app.lib import pyops
        mock_rq = Mock()
        mock_rq.put.return_value = {"schedule": {"id": 1}}
        schedule_control = pyops.ScheduleControl(mock_rq)
        result = schedule_control.create("test_set", "test_name", True, "* * * * *", "param")
        assert result == {"id": 1}
        mock_rq.put.assert_called_once_with("scriptlet/schedule/item", json={
            "set": "test_set",
            "name": "test_name",
            "drop_done": True,
            "max_retries": 0,
            "schedule": "* * * * *",
            "param": "param"
        })

    def test_get(self, mock_control_request):
        from app.lib import pyops
        mock_rq = Mock()
        mock_rq.get.return_value = {"schedule": {"active": True}}
        schedule_control = pyops.ScheduleControl(mock_rq)
        result = schedule_control.get(1)
        assert result == {"schedule": {"active": True}}
        mock_rq.get.assert_called_once_with("scriptlet/schedule/item", params={"id": 1})

    def test_delete(self, mock_control_request):
        from app.lib import pyops
        mock_rq = Mock()
        mock_rq.delete.return_value = {"deleted": True}
        schedule_control = pyops.ScheduleControl(mock_rq)
        result = schedule_control.delete(1)
        assert result == {"deleted": True}
        mock_rq.delete.assert_called_once_with("scriptlet/schedule/item", params={"id": 1})

    def test_activate(self, mock_control_request):
        from app.lib import pyops
        mock_rq = Mock()
        mock_rq.get.return_value = {"activated": True}
        schedule_control = pyops.ScheduleControl(mock_rq)
        result = schedule_control.activate(1)
        assert result == {"activated": True}
        mock_rq.get.assert_called_once_with("scriptlet/schedule/activate", params={"id": 1})

    def test_deactivate(self, mock_control_request):
        from app.lib import pyops
        mock_rq = Mock()
        mock_rq.get.return_value = {"deactivated": True}
        schedule_control = pyops.ScheduleControl(mock_rq)
        result = schedule_control.deactivate(1)
        assert result == {"deactivated": True}
        mock_rq.get.assert_called_once_with("scriptlet/schedule/deactivate", params={"id": 1})

class TestControl:
    def test_get(self, mock_control_request):
        from app.lib import pyops
        mock_rq = Mock()
        mock_rq.get.return_value = {"data": "value"}
        control = pyops.Control("test_user", "127.0.0.1:8000")
        control.rq = mock_rq  # Patch for test
        result = control.get("test_set", "test_name", params={"p": "v"})
        assert result == {"data": "value"}
        mock_rq.get.assert_called_once_with("controller/test_set/test_name", params={"p": "v"})

    def test_delete(self, mock_control_request):
        from app.lib import pyops
        mock_rq = Mock()
        mock_rq.delete.return_value = {"deleted": True}
        control = pyops.Control("test_user", "127.0.0.1:8000")
        control.rq = mock_rq
        result = control.delete("test_set", "test_name")
        assert result == {"deleted": True}
        mock_rq.delete.assert_called_once_with("controller/test_set/test_name", params={})

    def test_put(self, mock_control_request):
        from app.lib import pyops
        mock_rq = Mock()
        mock_rq.put.return_value = {"put": "success"}
        control = pyops.Control("test_user", "127.0.0.1:8000")
        control.rq = mock_rq
        result = control.put("test_set", "test_name", json={"data": "value"})
        assert result == {"put": "success"}
        mock_rq.put.assert_called_once_with("controller/test_set/test_name", json={"data": "value"})

    def test_post(self, mock_control_request):
        from app.lib import pyops
        mock_rq = Mock()
        mock_rq.post.return_value = {"posted": True}
        control = pyops.Control("test_user", "127.0.0.1:8000")
        control.rq = mock_rq
        result = control.post("test_set", "test_name", json={"data": "value"})
        assert result == {"posted": True}
        mock_rq.post.assert_called_once_with("controller/test_set/test_name", json={"data": "value"})

class TestServiceListener:
    @patch('os.remove')
    @patch('tempfile.gettempdir')
    @patch('zmq.Context')
    @patch('zmq.Poller')
    @patch('os.makedirs')
    @patch('signal.signal')
    @patch('atexit.register')
    def test_init(self, mock_atexit, mock_signal, mock_makedirs, mock_poller, mock_context, mock_gettempdir, mock_remove):
        from app.lib import pyops
        mock_atexit.side_effect = lambda *args: None
        mock_remove.return_value = None  # Suppress atexit remove error
        mock_gettempdir.return_value = '/tmp'
        mock_socket = Mock()
        mock_context.return_value.socket.return_value = mock_socket
        listener = pyops.ServiceListener("test_job", "test_owner")
        mock_context.assert_called_once()
        mock_socket.bind.assert_called_once_with("ipc:///tmp/pyops/test_owner.sock")
        mock_poller.assert_called_once_with()
        mock_poller.return_value.register.assert_called_once_with(mock_socket, flags=zmq.POLLIN)
        mock_signal.assert_any_call(signal.SIGINT, ANY)
        mock_signal.assert_any_call(signal.SIGTERM, ANY)
        mock_makedirs.assert_called_once_with("/tmp/pyops", exist_ok=True)
        assert listener.sockfile == "/tmp/pyops/test_owner.sock"
        assert listener.kill_now is False
        listener.close()

    @patch('os.remove')
    @patch('atexit.register')
    @patch('tempfile.gettempdir')
    @patch('zmq.Poller')
    @patch('zmq.Context')
    @patch('msgpack.unpackb')
    def test_listen_timeout_no_kill(self, mock_unpackb, mock_context, mock_poller, mock_gettempdir, mock_atexit, mock_remove):
        from app.lib import pyops
        mock_atexit.side_effect = lambda *args: None
        mock_remove.return_value = None
        mock_gettempdir.return_value = '/tmp'
        mock_socket = Mock()
        mock_context.return_value.socket.return_value = mock_socket
        listener = pyops.ServiceListener("test_job", "test_owner")
        mock_poller.return_value.poll.return_value = [(mock_socket, zmq.POLLIN)]  # Simulate input to exit loop
        mock_socket.recv.return_value = msgpack.packb({"cmd": "test"})
        mock_unpackb.return_value = {"cmd": "test"}
        result = listener.listen()
        assert result == {"cmd": "test"}
        mock_poller.return_value.poll.assert_called_once_with(timeout=1000)
        mock_unpackb.assert_called_once()
        listener.close()

    @patch('os.remove')
    @patch('atexit.register')
    @patch('tempfile.gettempdir')
    @patch('zmq.Poller')
    @patch('zmq.Context')
    def test_listen_kill_now(self, mock_context, mock_poller, mock_gettempdir, mock_atexit, mock_remove):
        from app.lib import pyops
        mock_atexit.side_effect = lambda *args: None
        mock_remove.return_value = None
        mock_gettempdir.return_value = '/tmp'
        listener = pyops.ServiceListener("test_job", "test_owner")
        listener.kill_now = True
        mock_poller.return_value.poll.return_value = []
        result = listener.listen()
        assert result == "TERM"
        mock_poller.return_value.poll.assert_called_once_with(timeout=1000)
        listener.socket.recv.assert_not_called()
        listener.close()

    @patch('os.remove')
    @patch('atexit.register')
    @patch('tempfile.gettempdir')
    @patch('zmq.Poller')
    @patch('zmq.Context')
    @patch('msgpack.unpackb')
    def test_listen_poll_input(self, mock_unpackb, mock_context, mock_poller, mock_gettempdir, mock_atexit, mock_remove):
        from app.lib import pyops
        mock_atexit.side_effect = lambda *args: None
        mock_remove.return_value = None
        mock_gettempdir.return_value = '/tmp'
        mock_socket = Mock()
        mock_context.return_value.socket.return_value = mock_socket
        listener = pyops.ServiceListener("test_job", "test_owner")
        mock_poller.return_value.poll.return_value = [(mock_socket, zmq.POLLIN)]
        mock_socket.recv.return_value = msgpack.packb({"cmd": "test"})
        mock_unpackb.return_value = {"cmd": "test"}
        result = listener.listen()
        assert result == {"cmd": "test"}
        mock_poller.return_value.poll.assert_called_once_with(timeout=1000)
        mock_unpackb.assert_called_once()
        listener.close()

    @patch('os.remove')
    @patch('atexit.register')
    @patch('tempfile.gettempdir')
    @patch('msgpack.packb')
    @patch('zmq.Context')
    def test_response(self, mock_context, mock_packb, mock_gettempdir, mock_atexit, mock_remove):
        from app.lib import pyops
        mock_atexit.side_effect = lambda *args: None
        mock_remove.return_value = None
        mock_gettempdir.return_value = '/tmp'
        mock_socket = Mock()
        mock_context.return_value.socket.return_value = mock_socket
        listener = pyops.ServiceListener("test_job", "test_owner")
        listener.last_identity = b'test_identity'  # Simulate identity from poll/listen
        test_msg = {"response": "data"}
        mock_packb.return_value = b'test_packed'
        listener.response(test_msg)
        mock_packb.assert_called_once_with([200, test_msg])
        # Check multipart send
        assert mock_socket.send.call_count == 3
        mock_socket.send.assert_any_call(b'test_identity', zmq.SNDMORE)
        mock_socket.send.assert_any_call(b'', zmq.SNDMORE)
        mock_socket.send.assert_any_call(b'test_packed')
        listener.close()

    @patch('os.remove')
    @patch('atexit.register')
    @patch('tempfile.gettempdir')
    @patch('msgpack.packb')
    @patch('zmq.Context')
    def test_response_with_status(self, mock_context, mock_packb, mock_gettempdir, mock_atexit, mock_remove):
        from app.lib import pyops
        mock_atexit.side_effect = lambda *args: None
        mock_remove.return_value = None
        mock_gettempdir.return_value = '/tmp'
        mock_socket = Mock()
        mock_context.return_value.socket.return_value = mock_socket
        listener = pyops.ServiceListener("test_job", "test_owner")
        listener.last_identity = b'test_identity'
        test_msg = "resource not found"
        mock_packb.return_value = b'test_packed'
        listener.response(test_msg, 404)
        mock_packb.assert_called_once_with([404, test_msg])
        assert mock_socket.send.call_count == 3
        listener.close()

    def test_exit_gracefully(self):
        from app.lib import pyops
        listener = pyops.ServiceListener("test_job", "test_owner")
        listener.exit_gracefully(None, None)
        assert listener.kill_now is True
        listener.close()

    @patch('os.remove')
    @patch('atexit.register')
    @patch('tempfile.gettempdir')
    @patch('zmq.Poller')
    @patch('zmq.Context')
    def test_poll_no_input(self, mock_context, mock_poller, mock_gettempdir, mock_atexit, mock_remove):
        from app.lib import pyops
        mock_atexit.side_effect = lambda *args: None
        mock_remove.return_value = None
        mock_gettempdir.return_value = '/tmp'
        mock_socket = Mock()
        mock_context.return_value.socket.return_value = mock_socket
        listener = pyops.ServiceListener("test_job", "test_owner")
        mock_poller.return_value.poll.return_value = []  # No events
        result = listener.poll()
        assert result is None
        mock_poller.return_value.poll.assert_called_once_with(timeout=0)
        mock_socket.recv.assert_not_called()
        listener.close()

    @patch('os.remove')
    @patch('atexit.register')
    @patch('tempfile.gettempdir')
    @patch('zmq.Poller')
    @patch('zmq.Context')
    @patch('msgpack.unpackb')
    def test_poll_with_input(self, mock_unpackb, mock_context, mock_poller, mock_gettempdir, mock_atexit, mock_remove):
        from app.lib import pyops
        mock_atexit.side_effect = lambda *args: None
        mock_remove.return_value = None
        mock_gettempdir.return_value = '/tmp'
        mock_socket = Mock()
        mock_context.return_value.socket.return_value = mock_socket
        listener = pyops.ServiceListener("test_job", "test_owner")
        mock_poller.return_value.poll.return_value = [(mock_socket, zmq.POLLIN)]
        test_msg = {"cmd": "test"}
        mock_socket.recv.return_value = msgpack.packb(test_msg)
        mock_unpackb.return_value = test_msg
        result = listener.poll()
        assert result == test_msg
        mock_poller.return_value.poll.assert_called_once_with(timeout=0)
        mock_unpackb.assert_called_once_with(mock_socket.recv.return_value)
        listener.close()

    @patch('os.remove')
    @patch('atexit.register')
    @patch('tempfile.gettempdir')
    @patch('zmq.Poller')
    @patch('zmq.Context')
    def test_poll_kill_now(self, mock_context, mock_poller, mock_gettempdir, mock_atexit, mock_remove):
        from app.lib import pyops
        mock_atexit.side_effect = lambda *args: None
        mock_remove.return_value = None
        mock_gettempdir.return_value = '/tmp'
        listener = pyops.ServiceListener("test_job", "test_owner")
        listener.kill_now = True
        result = listener.poll()
        assert result == "TERM"
        mock_poller.return_value.poll.assert_not_called()
        listener.socket.recv.assert_not_called()
        listener.close()

    @patch('os.remove')
    @patch('atexit.register')
    @patch('tempfile.gettempdir')
    @patch('zmq.Poller')
    @patch('zmq.Context')
    def test_async_poll_no_messages(self, mock_context, mock_poller, mock_gettempdir, mock_atexit, mock_remove):
        from app.lib import pyops
        mock_atexit.side_effect = lambda *args: None
        mock_remove.return_value = None
        mock_gettempdir.return_value = '/tmp'
        mock_socket = Mock()
        mock_context.return_value.socket.return_value = mock_socket
        listener = pyops.ServiceListener("test_job", "test_owner")
        mock_poller.return_value.poll.return_value = []  # No events
        result = listener.async_poll()
        assert result == []
        mock_poller.return_value.poll.assert_called_with(timeout=0)
        mock_socket.recv.assert_not_called()
        listener.close()

    @patch('os.remove')
    @patch('atexit.register')
    @patch('tempfile.gettempdir')
    @patch('msgpack.unpackb')
    @patch('zmq.Poller')
    @patch('zmq.Context')
    def test_async_poll_with_messages(self, mock_context, mock_poller, mock_unpackb, mock_gettempdir, mock_atexit, mock_remove):
        from app.lib import pyops
        mock_atexit.side_effect = lambda *args: None
        mock_remove.return_value = None
        mock_gettempdir.return_value = '/tmp'
        mock_socket = Mock()
        mock_context.return_value.socket.return_value = mock_socket
        listener = pyops.ServiceListener("test_job", "test_owner")
        # Simulate two messages
        mock_poller.return_value.poll.side_effect = [[(mock_socket, zmq.POLLIN)], [(mock_socket, zmq.POLLIN)], []]
        mock_socket.recv.side_effect = [b'identity1', b'', b'msg1', b'identity2', b'', b'msg2']
        mock_unpackb.side_effect = [{'cmd': 'test1'}, {'cmd': 'test2'}]
        result = listener.async_poll()
        assert result == [(b'identity1', {'cmd': 'test1'}), (b'identity2', {'cmd': 'test2'})]
        assert mock_socket.recv.call_count == 6  # 3 per message
        listener.close()

    @patch('os.remove')
    @patch('atexit.register')
    @patch('tempfile.gettempdir')
    @patch('zmq.Poller')
    @patch('zmq.Context')
    def test_async_poll_kill_now_returns_term_sentinel(self, mock_context, mock_poller, mock_gettempdir, mock_atexit, mock_remove):
        """Regression: when kill_now is set, async_poll must surface a
        (b"", "TERM") sentinel so non-blocking callers (e.g. causal tick
        loops) exit instead of polling forever past SIGTERM until the
        runner's 15s SIGKILL escalation. Mirrors listen()'s "TERM" return.
        """
        from app.lib import pyops
        mock_atexit.side_effect = lambda *args: None
        mock_remove.return_value = None
        mock_gettempdir.return_value = '/tmp'
        mock_socket = Mock()
        mock_context.return_value.socket.return_value = mock_socket
        listener = pyops.ServiceListener("test_job", "test_owner")
        listener.kill_now = True
        result = listener.async_poll()
        assert result == [(b"", "TERM")]
        # Must short-circuit: no poll, no recv.
        mock_poller.return_value.poll.assert_not_called()
        mock_socket.recv.assert_not_called()
        listener.close()

    @patch('os.remove')
    @patch('atexit.register')
    @patch('tempfile.gettempdir')
    @patch('msgpack.packb')
    @patch('zmq.Context')
    def test_async_response(self, mock_context, mock_packb, mock_gettempdir, mock_atexit, mock_remove):
        from app.lib import pyops
        mock_atexit.side_effect = lambda *args: None
        mock_remove.return_value = None
        mock_gettempdir.return_value = '/tmp'
        mock_socket = Mock()
        mock_context.return_value.socket.return_value = mock_socket
        listener = pyops.ServiceListener("test_job", "test_owner")
        test_identity = b'test_identity'
        test_msg = {"response": "async_data"}
        mock_packb.return_value = b'packed_async'
        listener.async_response(test_identity, test_msg)
        mock_packb.assert_called_once_with([200, test_msg])
        # Check multipart send
        assert mock_socket.send.call_count == 3
        mock_socket.send.assert_any_call(test_identity, zmq.SNDMORE)
        mock_socket.send.assert_any_call(b'', zmq.SNDMORE)
        mock_socket.send.assert_any_call(b'packed_async')
        listener.close()

    @patch('os.remove')
    @patch('atexit.register')
    @patch('tempfile.gettempdir')
    @patch('msgpack.packb')
    @patch('zmq.Context')
    def test_async_response_with_status(self, mock_context, mock_packb, mock_gettempdir, mock_atexit, mock_remove):
        from app.lib import pyops
        mock_atexit.side_effect = lambda *args: None
        mock_remove.return_value = None
        mock_gettempdir.return_value = '/tmp'
        mock_socket = Mock()
        mock_context.return_value.socket.return_value = mock_socket
        listener = pyops.ServiceListener("test_job", "test_owner")
        test_identity = b'test_identity'
        test_msg = "forbidden"
        mock_packb.return_value = b'packed_async'
        listener.async_response(test_identity, test_msg, 403)
        mock_packb.assert_called_once_with([403, test_msg])
        assert mock_socket.send.call_count == 3
        listener.close()

class TestServiceClient:
    @patch('zmq.Context')
    @patch('os.path.join')
    def test_init_success(self, mock_join, mock_context):
        from app.lib import pyops
        mock_job = "123"
        mock_join.return_value = "/tmp/pyops/123.sock"
        client = pyops.ServiceClient(mock_job)
        assert client.job_id == mock_job
        mock_context.assert_called_once()
        client.socket.connect.assert_called_once_with("ipc:///tmp/pyops/123.sock")

    @patch('msgpack.unpackb')
    @patch('msgpack.packb')
    @patch('zmq.Context')
    def test_request_success(self, mock_context, mock_packb, mock_unpackb):
        from app.lib import pyops
        mock_job = "123"
        client = pyops.ServiceClient(mock_job)
        test_msg = {"cmd": "test"}
        mock_packb.return_value = b'test_packed'
        mock_socket = client.socket
        mock_socket.recv.return_value = b'reply_packed'
        mock_unpackb.return_value = [200, {"reply": "ok"}]
        status, data = client.request(test_msg)
        assert status == 200
        assert data == {"reply": "ok"}
        mock_packb.assert_called_once_with(test_msg)
        mock_socket.send.assert_called_once_with(b'test_packed')
        mock_socket.recv.assert_called_once()
        mock_unpackb.assert_called_once_with(b'reply_packed')

    @patch('msgpack.unpackb')
    @patch('msgpack.packb')
    @patch('zmq.Context')
    def test_request_error_status(self, mock_context, mock_packb, mock_unpackb):
        from app.lib import pyops
        mock_job = "123"
        client = pyops.ServiceClient(mock_job)
        mock_packb.return_value = b'test_packed'
        mock_socket = client.socket
        mock_socket.recv.return_value = b'reply_packed'
        mock_unpackb.return_value = [422, "validation failed"]
        status, data = client.request({"cmd": "test"})
        assert status == 422
        assert data == "validation failed"

    @patch('msgpack.packb')
    @patch('zmq.Context')
    def test_request_recv_error(self, mock_context, mock_packb):
        from app.lib import pyops
        mock_job = "123"
        client = pyops.ServiceClient(mock_job)
        mock_packb.return_value = b'test_packed'
        client.socket.recv.side_effect = zmq.ZMQError()
        with pytest.raises(zmq.ZMQError):
            client.request({"cmd": "test"})
        mock_packb.assert_called_once_with({"cmd": "test"})
        client.socket.send.assert_called_once_with(b'test_packed')
        client.socket.recv.assert_called_once()

    @patch('atexit.register')
    @patch('zmq.Context')
    def test_init_does_not_register_atexit(self, mock_context, mock_register):
        """Regression (ZMQ thread leak): the per-request ServiceClient must
        NOT defer teardown to atexit. That deferral pinned every context —
        and its ZMQbg IO/reaper thread pair — for the whole worker lifetime,
        the KernelStack/thread creep. Teardown is caller-owned via close()."""
        from app.lib import pyops
        client = pyops.ServiceClient("123")
        mock_register.assert_not_called()
        client.close()

    @patch('zmq.Context')
    def test_init_sets_linger_always_and_rcvtimeo_when_given(self, mock_context):
        from app.lib import pyops
        # No timeout supplied: LINGER 0 set, RCVTIMEO left unset (legacy
        # infinite block preserved for in-process callers).
        sock1 = Mock()
        mock_context.return_value.socket.return_value = sock1
        c1 = pyops.ServiceClient("j1")
        sock1.setsockopt.assert_any_call(zmq.LINGER, 0)
        assert not any(call.args and call.args[0] == zmq.RCVTIMEO
                       for call in sock1.setsockopt.call_args_list)
        c1.close()
        # Timeout supplied: RCVTIMEO bound to the given ms.
        sock2 = Mock()
        mock_context.return_value.socket.return_value = sock2
        c2 = pyops.ServiceClient("j2", recv_timeout_ms=5000)
        sock2.setsockopt.assert_any_call(zmq.RCVTIMEO, 5000)
        c2.close()

    @patch('zmq.Context')
    def test_close_terms_context_and_second_close_raises(self, mock_context):
        """close() reaps the socket + context; closing a second time is a
        caller bug and raises instead of passing silently."""
        from app.lib import pyops
        sock = Mock()
        mock_context.return_value.socket.return_value = sock
        client = pyops.ServiceClient("123")
        client.close()
        sock.close.assert_called_once()
        mock_context.return_value.term.assert_called_once()
        with pytest.raises(RuntimeError, match="ServiceClient for job 123 is already closed"):
            client.close()
        sock.close.assert_called_once()

    @patch('zmq.Context')
    def test_close_errors_surface(self, mock_context):
        from app.lib import pyops
        mock_context.return_value.term.side_effect = zmq.ZMQError("term failed")
        client = pyops.ServiceClient("123")
        with pytest.raises(zmq.ZMQError, match="term failed"):
            client.close()

    @patch('zmq.Context')
    def test_context_manager_closes_on_exit(self, mock_context):
        from app.lib import pyops
        sock = Mock()
        mock_context.return_value.socket.return_value = sock
        with pyops.ServiceClient("123") as c:
            assert c.job_id == "123"
        sock.close.assert_called_once()
        mock_context.return_value.term.assert_called_once()

# Edges: ZMQ timeouts/retries not explicit in code, but mock poll/recv fails; HTTP 500/409 via handle_response; no jobs exception covered.
