"""Write each composite action's `run:` scripts to <dir>/<action>-<step>.sh so
shellcheck can check them (expressions replaced by a placeholder)."""

from pathlib import Path
import re
import sys

import yaml

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
for action in sorted(Path(__file__).resolve().parent.parent.glob("*/action.yml")):
    for index, step in enumerate(yaml.safe_load(action.read_text())["runs"]["steps"]):
        # Bash only: shellcheck cannot read PowerShell (`shell: pwsh`).
        if "run" in step and step.get("shell", "bash") == "bash":
            body = re.sub(r"\$\{\{[^}]*\}\}", "X", step["run"])
            (out / f"{action.parent.name}-{index}.sh").write_text("#!/usr/bin/env bash\n" + body)
