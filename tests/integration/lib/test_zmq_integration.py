import pytest
import threading
import time
import tempfile
import os
from unittest.mock import patch, MagicMock

from app.lib.pyops import ServiceListener, ServiceClient


class TestZMQIntegration:
    """Integration tests for real ZMQ communication between ServiceListener and ServiceClient"""

    def test_sync_messaging(self):
        """Test synchronous request-response messaging"""
        owner = "test_user"
        job_id = "test_job_123"

        # Start listener in background thread
        listener = ServiceListener(owner, job_id)
        stop_event = threading.Event()

        def service_loop():
            while not stop_event.is_set():
                msg = listener.poll()
                if msg == "TERM":
                    break
                if msg:
                    # Echo back the message with "echoed:" prefix
                    response = f"echoed:{msg.get('data', '')}"
                    listener.response({"result": response})

        service_thread = threading.Thread(target=service_loop, daemon=True)
        service_thread.start()

        try:
            # Give listener time to bind
            time.sleep(0.1)

            # Connect client
            client = ServiceClient(job_id)

            # Send sync message
            test_data = "hello world"
            status, data = client.request({
                "type": "POST",
                "path": "/test",
                "data": test_data,
                "data_type": "text"
            })

            assert status == 200
            assert data["result"] == f"echoed:{test_data}"

        finally:
            # Cleanup
            stop_event.set()
            listener.kill_now = True
            service_thread.join(timeout=1.0)
            listener.close()
            if hasattr(client, 'close'):
                client.close()

    def test_async_messaging(self):
        """Test asynchronous messaging with multiple concurrent requests"""
        owner = "test_user"
        job_id = "test_job_123"

        # Start listener in background thread
        listener = ServiceListener(owner, job_id)
        stop_event = threading.Event()

        def async_service_loop():
            while not stop_event.is_set():
                messages = listener.async_poll()
                for identity, msg in messages:
                    # Process message asynchronously
                    data = msg.get('data', '')
                    response = f"async_echoed:{data}"
                    listener.async_response(identity, {"result": response})

        service_thread = threading.Thread(target=async_service_loop, daemon=True)
        service_thread.start()

        try:
            # Give listener time to bind
            time.sleep(0.1)

            # Connect client
            client = ServiceClient(job_id)

            # Send multiple messages quickly (before responses)
            test_messages = ["msg1", "msg2", "msg3"]
            responses = []

            for msg_data in test_messages:
                status, data = client.request({
                    "type": "POST",
                    "path": "/test",
                    "data": msg_data,
                    "data_type": "text"
                })
                assert status == 200
                responses.append(data["result"])

            # Verify all responses received
            expected = [f"async_echoed:{msg}" for msg in test_messages]
            assert responses == expected

        finally:
            # Cleanup
            stop_event.set()
            listener.kill_now = True
            service_thread.join(timeout=1.0)
            listener.close()
            if hasattr(client, 'close'):
                client.close()

    def test_error_status_propagation(self):
        """Test that non-200 status codes are propagated from listener to client"""
        owner = "test_user"
        job_id = "test_job_error"

        listener = ServiceListener(owner, job_id)
        stop_event = threading.Event()

        def service_loop():
            while not stop_event.is_set():
                msg = listener.poll()
                if msg == "TERM":
                    break
                if msg:
                    if msg.get("path") == "/forbidden":
                        listener.response("access denied", 403)
                    elif msg.get("path") == "/missing":
                        listener.response("not found", 404)
                    else:
                        listener.response({"ok": True})

        service_thread = threading.Thread(target=service_loop, daemon=True)
        service_thread.start()

        try:
            time.sleep(0.1)
            client = ServiceClient(job_id)

            status, data = client.request({"path": "/forbidden"})
            assert status == 403
            assert data == "access denied"

            status, data = client.request({"path": "/missing"})
            assert status == 404
            assert data == "not found"

            status, data = client.request({"path": "/ok"})
            assert status == 200
            assert data == {"ok": True}

        finally:
            stop_event.set()
            listener.kill_now = True
            service_thread.join(timeout=1.0)
            listener.close()
            if hasattr(client, 'close'):
                client.close()

    def test_async_poll_surfaces_term_to_nonblocking_loop(self):
        """Regression: a non-blocking causal-style loop driven by
        async_poll() must see the TERM sentinel when SIGTERM lands, exit
        promptly, and not race against the runner's 15s SIGKILL.

        Pre-fix, async_poll() ignored kill_now → the loop never saw TERM
        → in production the runner SIGKILL'd at 15s and the row was
        marked `failed`. This integration test drives the real signal
        handler (exit_gracefully → kill_now=True) so the path between
        signal arrival and TERM delivery is exercised end-to-end.
        """
        import signal as _signal

        listener = ServiceListener("test_user", "test_async_term")
        stop_event = threading.Event()
        observed = {"saw_term": False, "exit_at": None, "iterations": 0}

        def causal_style_loop():
            # Mirror the production pattern in
            # system::causal.library.py / PyOpsListenerVortex.poll().
            while not stop_event.is_set():
                observed["iterations"] += 1
                for _identity, msg in listener.async_poll():
                    if msg == "TERM":
                        observed["saw_term"] = True
                        observed["exit_at"] = time.monotonic()
                        return
                time.sleep(0.05)

        thread = threading.Thread(target=causal_style_loop, daemon=True)
        thread.start()
        try:
            # Let the loop run for a few iterations so we know it's
            # genuinely sitting in the async_poll path, not exiting on
            # initialisation.
            time.sleep(0.2)
            assert observed["iterations"] > 1, "loop never started polling"

            # Drive the real SIGTERM handler. ServiceListener installed
            # signal.signal(signal.SIGTERM, self.exit_gracefully) at
            # __init__, so this is the same code path production hits.
            sent_at = time.monotonic()
            os.kill(os.getpid(), _signal.SIGTERM)

            # The loop should observe TERM and return within a couple of
            # poll cycles (each 50ms). 2s is a generous ceiling that is
            # nonetheless an order of magnitude below the 15s SIGKILL
            # window the regression burned through.
            thread.join(timeout=2.0)
            assert not thread.is_alive(), (
                "loop did not exit within 2s of SIGTERM — async_poll "
                "is not surfacing kill_now as the (b'', 'TERM') sentinel"
            )
            assert observed["saw_term"], "loop returned without seeing TERM"

            elapsed = observed["exit_at"] - sent_at
            assert elapsed < 1.0, (
                f"loop took {elapsed:.3f}s to see TERM — should be ~one poll cycle"
            )

        finally:
            stop_event.set()
            if thread.is_alive():
                thread.join(timeout=1.0)
            listener.close()
