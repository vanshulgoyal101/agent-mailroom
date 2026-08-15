import os
import sys
import subprocess
import tempfile
import time
import pytest
from web3 import Web3

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TEST_PORT = 8546
TEST_RPC_URL = f"http://127.0.0.1:{TEST_PORT}"

# Fixtures whose presence marks a test as requiring the local sandbox node.
_INTEGRATION_FIXTURES = {"sandbox_node", "w3"}


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: test requires the local sandbox_node JSON-RPC server.",
    )


def pytest_collection_modifyitems(items):
    """Auto-tag tests that depend on the sandbox node so they can be deselected.

    Run only unit tests with:  pytest -m "not integration"
    """
    for item in items:
        if _INTEGRATION_FIXTURES.intersection(getattr(item, "fixturenames", ())):
            item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session")
def sandbox_node():
    """Fixture to start sandbox_node.py in the background for testing."""
    node_script = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sandbox_node.py")

    # Capture the node's stderr so startup failures produce actionable messages.
    stderr_file = tempfile.TemporaryFile()
    proc = subprocess.Popen(
        [sys.executable, "-u", node_script, str(TEST_PORT)],
        stdout=subprocess.DEVNULL,
        stderr=stderr_file,
    )

    # Wait for startup by trying to connect to the port
    import socket
    start_time = time.time()
    success = False
    while time.time() - start_time < 5.0:
        try:
            with socket.create_connection(("127.0.0.1", TEST_PORT), timeout=0.5):
                success = True
                break
        except OSError:
            time.sleep(0.1)

    if not success or proc.poll() is not None:
        proc.kill()
        stderr_file.seek(0)
        node_err = stderr_file.read().decode(errors="replace").strip()
        stderr_file.close()
        detail = f"\nsandbox_node stderr:\n{node_err}" if node_err else ""
        raise RuntimeError(
            f"Failed to start sandbox_node on port {TEST_PORT}: port timeout or process died.{detail}"
        )

    yield TEST_RPC_URL

    # Terminate server
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    finally:
        stderr_file.close()


@pytest.fixture(scope="session")
def w3(sandbox_node):
    """Fixture to get a Web3 instance pointing to the test sandbox node."""
    return Web3(Web3.HTTPProvider(sandbox_node))
