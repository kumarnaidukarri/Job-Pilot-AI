Manually trigger one JobPilot AI pipeline run right now (scrape → score → export → email).

This runs a single pipeline execution outside the scheduler — useful when you want fresh jobs immediately without waiting for the next scheduled run.

Steps:
1. Confirm with the user that they want to start a pipeline run (it uses free LLM quota and takes ~5–12 minutes)
2. Run the pipeline using the Bash tool:

```bash
cd "c:/Users/ashok/Desktop/JobPilot_AI" && python -c "
import os, sys
# run from the repo root (cwd)
sys.path.insert(0, '.')
from scheduler import run_pipeline
run_pipeline()
"
```

3. Watch for output and report back: how many jobs were scraped, how many matched (score >= 7), and whether the email was sent successfully.
4. If there are errors, diagnose them and suggest fixes.

Note: This does NOT start the repeating scheduler — it runs exactly one pass and finishes.
