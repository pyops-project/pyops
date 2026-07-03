"""Unit tests for job_lib.send_completion ZMQ lifecycle.

Covers the hardening added for the ZMQ thread-leak/hang bug: LINGER 0 +
bounded RCVTIMEO on the REQ socket, the timeout (zmq.Again) path being
swallowed rather than raised, and the context always being terminated so
no ZMQbg thread pair is orphaned.
"""
import zmq
from unittest.mock import patch, Mock


class TestSendCompletion:
    @patch('zmq.Context')
    def test_sets_linger_zero_and_bounded_rcvtimeo(self, mock_context):
        from app.lib import job as job_lib
        sock = Mock()
        mock_context.return_value.socket.return_value = sock
        job_lib.send_completion("/tmp/pyops/runner.sock", {"id": "j1", "state": "done"})
        sock.setsockopt.assert_any_call(zmq.LINGER, 0)
        sock.setsockopt.assert_any_call(zmq.RCVTIMEO, job_lib.ACK_TIMEOUT_MS)

    @patch('zmq.Context')
    def test_happy_path_recvs_ack_and_terms_context(self, mock_context):
        from app.lib import job as job_lib
        sock = Mock()
        sock.recv.return_value = b'ack'
        mock_context.return_value.socket.return_value = sock
        job_lib.send_completion("/tmp/pyops/runner.sock", {"id": "j2", "state": "done"})
        sock.send.assert_called_once()
        sock.recv.assert_called_once()
        sock.close.assert_called_once()
        mock_context.return_value.term.assert_called_once()

    @patch('zmq.Context')
    def test_missing_ack_timeout_is_swallowed_not_raised(self, mock_context):
        """Regression (hang): a missing ACK (runner busy/restarting) must
        NOT block or raise — send_completion returns and still tears down
        the context so the job subprocess can exit cleanly."""
        from app.lib import job as job_lib
        sock = Mock()
        sock.recv.side_effect = zmq.Again()  # RCVTIMEO elapsed
        mock_context.return_value.socket.return_value = sock
        # Must not raise.
        job_lib.send_completion("/tmp/pyops/runner.sock", {"id": "j3", "state": "failed"})
        sock.close.assert_called_once()
        mock_context.return_value.term.assert_called_once()

    @patch('zmq.Context')
    def test_zmqerror_is_logged_and_context_termed(self, mock_context):
        from app.lib import job as job_lib
        sock = Mock()
        sock.recv.side_effect = zmq.ZMQError("boom")
        mock_context.return_value.socket.return_value = sock
        job_lib.send_completion("/tmp/pyops/runner.sock", {"id": "j4", "state": "done"})
        sock.close.assert_called_once()
        mock_context.return_value.term.assert_called_once()
