"""Entry point: start the Streamlit UI (run from CLI: streamlit run run_ui.py)."""

import sys
from pathlib import Path

# Ensure the project root is on sys.path
project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

if __name__ == "__main__":
    from streamlit.web import cli as stcli
    sys.argv = ["streamlit", "run", str(project_root / "ui" / "app.py"), "--server.port=8501"]
    sys.exit(stcli.main())
