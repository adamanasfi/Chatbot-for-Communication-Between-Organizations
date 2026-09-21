import sys
from pathlib import Path

# Make civildefensev2's own modules (tools, vehicle_tracking_tools, agent,
# ...) importable regardless of where pytest is invoked from, matching how
# server.py resolves imports.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
