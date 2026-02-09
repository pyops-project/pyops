import pytest
import threading
import time
import tempfile
import os
from unittest.mock import patch, MagicMock

from app.lib.pyops import ServiceListener, ServiceClient


@pytest.fixture
def mock_runner_find():
    """Mock runner.find to return a fake job ID"""
    with patch('app.lib.runner.find') as mock_find:
        mock_find.return_value = ["test_job_123"]
        yield mock_find


class TestZMQIntegration:
    """Integration tests for real ZMQ communication between ServiceListener and ServiceClient"""

    def test_sync_messaging(self, mock_runner_find):
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
            client = ServiceClient(owner, "test_set", "test_name")

            # Send sync message
            test_data = "hello world"
            response = client.request({
                "type": "POST",
                "path": "/test",
                "data": test_data,
                "data_type": "text"
            })

            assert response["result"] == f"echoed:{test_data}"

        finally:
            # Cleanup
            stop_event.set()
            listener.kill_now = True
            service_thread.join(timeout=1.0)
            listener.close()
            if hasattr(client, 'close'):
                client.close()

    def test_async_messaging(self, mock_runner_find):
        """Test asynchronous messaging with multiple concurrent requests"""
        owner = "test_user"
        job_id = "test_job_123"  # Must match mock_runner_find return value

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
            client = ServiceClient(owner, "test_set", "test_name")

            # Send multiple messages quickly (before responses)
            test_messages = ["msg1", "msg2", "msg3"]
            responses = []

            for msg_data in test_messages:
                response = client.request({
                    "type": "POST",
                    "path": "/test",
                    "data": msg_data,
                    "data_type": "text"
                })
                responses.append(response["result"])

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

