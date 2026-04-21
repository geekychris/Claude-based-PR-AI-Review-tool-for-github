"""Manage the code_graph_search Java process lifecycle."""

from __future__ import annotations

import logging
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from review_tool.config import GraphConfig
from review_tool.graph_client import GraphClient

log = logging.getLogger(__name__)


def generate_graph_config(repo_path: str, data_dir: str | None = None) -> Path:
    """Generate a temporary config.yaml for code_graph_search."""
    if data_dir is None:
        data_dir = tempfile.mkdtemp(prefix="cgs_data_")

    config_content = f"""\
storage:
  dataDir: "{data_dir}"

repositories:
  - id: "review_target"
    name: "review_target"
    path: "{repo_path}"
    languages:
      - JAVA
      - GO
      - RUST
      - TYPESCRIPT
      - JAVASCRIPT
      - C
      - CPP
      - PYTHON

server:
  port: 8080
"""
    config_path = Path(tempfile.mktemp(prefix="cgs_config_", suffix=".yaml"))
    config_path.write_text(config_content)
    return config_path


def start_graph_server(
    config: GraphConfig,
    repo_path: str,
) -> subprocess.Popen:
    """Start code_graph_search as a subprocess and return the process handle."""
    if not config.jar_path:
        raise ValueError("graph.jar_path must be set to start code_graph_search")

    jar = Path(config.jar_path)
    if not jar.exists():
        raise FileNotFoundError(f"code_graph_search JAR not found: {jar}")

    # Generate config if not provided
    if config.config_path:
        cfg_path = config.config_path
    else:
        cfg_path = str(generate_graph_config(repo_path))

    cmd = [
        "java",
        "--enable-preview",
        "-jar",
        str(jar),
        "--config",
        cfg_path,
    ]

    log.info("Starting code_graph_search: %s", " ".join(cmd))
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return process


def wait_for_ready(
    host: str = "http://localhost:8080",
    timeout: int = 120,
) -> bool:
    """Poll the health endpoint until the server is ready or timeout."""
    client = GraphClient(base_url=host)
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if client.healthy():
            log.info("code_graph_search is ready at %s", host)
            client.close()
            return True
        time.sleep(2)

    client.close()
    log.error("code_graph_search did not become ready within %ds", timeout)
    return False


def stop_graph_server(process: subprocess.Popen, timeout: int = 10) -> None:
    """Gracefully stop the code_graph_search process."""
    if process.poll() is not None:
        return

    log.info("Stopping code_graph_search (pid=%d)", process.pid)
    process.send_signal(signal.SIGTERM)
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        log.warning("code_graph_search did not stop gracefully, killing")
        process.kill()
        process.wait(timeout=5)


def generate_mcp_config(jar_path: str, config_path: str) -> Path:
    """Generate an MCP config JSON for passing to claude --mcp-config."""
    import json

    mcp_cfg = {
        "mcpServers": {
            "code_graph": {
                "command": "java",
                "args": [
                    "--enable-preview",
                    "-jar",
                    jar_path,
                    "--mcp-stdio",
                    "--config",
                    config_path,
                ],
            }
        }
    }

    path = Path(tempfile.mktemp(prefix="mcp_config_", suffix=".json"))
    path.write_text(json.dumps(mcp_cfg, indent=2))
    return path
