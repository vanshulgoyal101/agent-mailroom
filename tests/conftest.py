import os
import sys
import subprocess
import time
import pytest
from web3 import Web3

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TEST_PORT = 8546
TEST_RPC_URL = f"http://127.0.0.1:{TEST_PORT}"


@pytest.fixture(scope="session")
def sandbox_node():
    """Fixture to start sandbox_node.py in the background for testing."""
    node_script = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sandbox_node.py")
    
    # Start server
    proc = subprocess.Popen(
        [sys.executable, "-u", node_script, str(TEST_PORT)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
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
        raise RuntimeError("Failed to start sandbox_node for tests: port timeout or process died")
        
    yield TEST_RPC_URL
    
    # Terminate server
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


@pytest.fixture(scope="session")
def w3(sandbox_node):
    """Fixture to get a Web3 instance pointing to the test sandbox node."""
    return Web3(Web3.HTTPProvider(sandbox_node))
