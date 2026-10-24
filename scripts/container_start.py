import os
import sys

from dotenv import load_dotenv

load_dotenv("/state/runtime.env")
if sys.argv[1] == "api":
    os.execvp(
        "uvicorn",
        ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"],
    )
else:
    os.execvp("python", ["python", "-m", "apps.dashboard.app"])
