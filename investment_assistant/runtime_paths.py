from pathlib import Path


RUNTIME_BASE = Path("/opt/hermes-investment-assistant")
CONFIG_DIR = RUNTIME_BASE / "config"
DEFAULT_CONFIG_PATH = CONFIG_DIR / "investment-assistant.json"

DATA_BASE = Path("/srv/investment-assistant")
DEFAULT_FILINGS_DIR = DATA_BASE / "filings"
