"""Install a built wheel and locked runtime dependencies outside the checkout."""

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    wheel = parser.parse_args().wheel.resolve(strict=True)
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="autohedge-wheel-") as directory:
        temporary = Path(directory)
        (temporary / ".env").touch()
        requirements = temporary / "runtime.txt"
        environment = temporary / "venv"
        subprocess.run(
            [
                "uv",
                "export",
                "--locked",
                "--no-dev",
                "--no-emit-project",
                "--output-file",
                str(requirements),
            ],
            cwd=root,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        subprocess.run(
            ["uv", "venv", "--python", sys.executable, str(environment)], check=True
        )
        binaries = environment / ("Scripts" if os.name == "nt" else "bin")
        python = binaries / ("python.exe" if os.name == "nt" else "python")
        subprocess.run(
            ["uv", "pip", "sync", "--python", str(python), str(requirements)],
            check=True,
        )
        subprocess.run(
            ["uv", "pip", "install", "--python", str(python), "--no-deps", str(wheel)],
            check=True,
        )
        subprocess.run(["uv", "pip", "check", "--python", str(python)], check=True)
        child_env = os.environ.copy()
        child_env.pop("PYTHONPATH", None)
        child_env.update(
            SWARMS_TELEMETRY_ON="false",
            LITELLM_LOCAL_MODEL_COST_MAP="True",
            WORKSPACE_DIR=str(temporary / "workspace"),
        )
        for name in (
            "OPENAI_API_KEY",
            "AUTOHEDGE_OPENAI_API_KEY",
            "EXA_API_KEY",
            "JUPITER_API_KEY",
            "SOLANA_PRIVATE_KEY",
            "MASSIVE_API_KEY",
            "COINGECKO_PRO_API_KEY",
        ):
            child_env.pop(name, None)
        subprocess.run(
            [
                str(python),
                "-c",
                """
import socket
network_attempts = []
def deny_network(*args, **kwargs):
    network_attempts.append(True)
    raise AssertionError('Installed wheel must initialize without network access')
socket.socket.connect = deny_network
socket.socket.connect_ex = deny_network
socket.create_connection = deny_network
from pathlib import Path
import sys
import autohedge
assert Path(autohedge.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
from autohedge.data.coingecko import CoinGeckoCandleProvider
from autohedge.data.history import HistoryStore
from autohedge.config import Settings
from autohedge.indicators import IndicatorConfig, calculate_indicators
from autohedge.snapshots import build_research_snapshot, collect_research_snapshot, parse_research_snapshot
assert IndicatorConfig().minimum_candles == 34
assert "swarms" not in sys.modules
assert CoinGeckoCandleProvider(Settings()).max_pages == 10
assert HistoryStore("history").directory == Path("history")
from autohedge import AutoHedge
from autohedge.workers import build_agent_team
team = build_agent_team()
assert team.director is not team.quant
assert not team.director.handoffs
system = AutoHedge(output_dir='outputs')
system.conversation.add(role='user', content='installed wheel check')
assert 'installed wheel check' in str(system.conversation.return_messages_as_list())
assert not network_attempts, 'Unexpected network attempt'
""",
            ],
            cwd=temporary,
            env=child_env,
            check=True,
            timeout=90,
        )
        for arguments in (["--help"], ["--version"]):
            subprocess.run(
                [str(binaries / "autohedge"), *arguments],
                cwd=temporary,
                env=child_env,
                check=True,
                timeout=15,
            )
        print(
            "Installed wheel, locked dependencies and CLI passed outside the checkout."
        )


if __name__ == "__main__":
    main()
