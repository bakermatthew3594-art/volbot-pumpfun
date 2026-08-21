#!/bin/bash
# ══════════════════════════════════════════════════════════════
# Hermes Skills Export Script
# Exports ALL skills from this Hermes instance to a transfer package
# that can be imported by PC Hermes.
#
# Usage: ./export-skills.sh
# ══════════════════════════════════════════════════════════════
set -e

SOURCE_DIR="/root/.hermes/skills"
EXPORT_DIR="/tmp/hermes-skills-export"
TIMESTAMP=$(date +%Y%m%d-%H%M)

echo "=== Hermes Skills Export"
echo "Source: $SOURCE_DIR"
echo "Export: $EXPORT_DIR"
echo "Time: $(date '+%Y-%m-%d %H:%M EDT')"
echo ""

# Create export directory
rm -rf "$EXPORT_DIR"
mkdir -p "$EXPORT_DIR"

# Copy all skills
cp -r "$SOURCE_DIR"/* "$EXPORT_DIR/"

# Count exported skills
SKILL_COUNT=$(find "$EXPORT_DIR" -name "SKILL.md" -maxdepth 5 2>/dev/null | wc -l)
DIR_COUNT=$(find "$EXPORT_DIR" -name "SKILL.md" -maxdepth 5 2>/dev/null | xargs -I{} dirname {} 2>/dev/null | sort -u | wc -l)

echo "Exported $SKILL_COUNT SKILL.md files across $DIR_COUNT skill directories."
echo ""

# Create a manifest of all skills
echo "=== Skills Manifest ===" > "$EXPORT_DIR/MANIFEST.md"
echo "" >> "$EXPORT_DIR/MANIFEST.md"
echo "Generated: $(date '+%Y-%m-%d %H:%M EDT')" >> "$EXPORT_DIR/MANIFEST.md"
echo "Total SKILL.md files: $SKILL_COUNT" >> "$EXPORT_DIR/MANIFEST.md"
echo "" >> "$EXPORT_DIR/MANIFEST.md"
echo "## Skill Index" >> "$EXPORT_DIR/MANIFEST.md"
echo "" >> "$EXPORT_DIR/MANIFEST.md"
find "$EXPORT_DIR" -name "SKILL.md" -maxdepth 5 2>/dev/null | sort | while read -r f; do
    rel=$(echo "$f" | sed "s|$EXPORT_DIR/||")
    name=$(basename "$(dirname "$f")")
    echo "  - \`$name\`: \`$rel\`" >> "$EXPORT_DIR/MANIFEST.md"
done

echo ""
echo "Manifest written to $EXPORT_DIR/MANIFEST.md"

# Also export cron jobs
echo ""
echo "=== Exporting Cron Jobs ==="
if [ -f "/root/.hermes/cron/jobs.json" ]; then
    cp /root/.hermes/cron/jobs.json "$EXPORT_DIR/cron-jobs.json"
    echo "Cron jobs exported to $EXPORT_DIR/cron-jobs.json"
    python3 -c "
import json
with open('$EXPORT_DIR/cron-jobs.json') as f:
    jobs = json.load(f)
if isinstance(jobs, dict) and 'jobs' in jobs:
    for j in jobs['jobs']:
        print(f'  - {j.get(\"name\",\"?\")}: schedule={j.get(\"schedule\",{}).get(\"display\",\"?\")}, enabled={j.get(\"enabled\",False)}')
elif isinstance(jobs, list):
    for j in jobs:
        if isinstance(j, dict):
            sched = j.get('schedule',{})
            expr = sched.get('expr','') if isinstance(sched,dict) else sched.get('display','') if isinstance(sched,dict) else '?'
            print(f'  - {j.get(\"name\",\"?\")}: schedule={expr}, enabled={j.get(\"enabled\",False)}')
" 2>/dev/null || true
fi

# Also export config
if [ -f "/root/.hermes/config.yaml" ]; then
    cp /root/.hermes/config.yaml "$EXPORT_DIR/hermes-config.yaml"
    # Redact sensitive values
    sed -i 's/key_env:.*/key_env: [REDACTED]/g' "$EXPORT_DIR/hermes-config.yaml"
    sed -i 's/base_url:.*/base_url: [REDACTED]/g' "$EXPORT_DIR/hermes-config.yaml" 2>/dev/null || true
    echo "Config exported to $EXPORT_DIR/hermes-config.yaml"
fi

# Create the import instructions
cat > "$EXPORT_DIR/IMPORT_INSTRUCTIONS.md" << 'EOF'
# Hermes Skills Import Instructions

## For PC Hermes Desktop

1. **Transfer this directory to PC:**
   ```bash
   # If using SCP from Android to PC:
   scp -r /tmp/hermes-skills-export user@pc:/tmp/
   
   # Or via USB transfer
   ```

2. **Import skills on PC Hermes:**
   ```bash
   # Copy skills directory to PC Hermes skills location
   cp -r /tmp/hermes-skills-export/skills/* /root/.hermes/skills/
   
   # Or use Hermes CLI (if available):
   hermes skills import /tmp/hermes-skills-export/
   ```

3. **Import cron jobs (optional):**
   - The cron jobs are in `cron-jobs.json`
   - Review each job before enabling
   - Some jobs reference Android-specific paths and should be skipped or updated

4. **Review and update:**
   - Check `MANIFEST.md` for complete skill listing
   - Review `IMPORT_INSTRUCTIONS.md` for any platform-specific notes
   - Update any Android-specific paths in skills (e.g., `/data/data/com.termux/...`)

## Platform Notes

Skills exported from this Android/Termux Hermes instance may contain:
- Android-specific paths (`/data/data/com.termux/files/usr/...`)
- Termux environment references
- Android storage access notes

PC Hermes should review and adapt these as needed.

## Available Skill Categories

- android (Termux, storage, device control)
- dev (Solana visualization, code tools)
- devops (packaging, testing, launch scripts)
- legal (GRS property recovery, citations, audits)
- mobile (file access, desktop control)
- productivity (Teams, meeting pipelines)
- research (web research, market data)
- tooling (browser extension, telegram gateway, knowledge forge)
EOF

echo ""
echo "Import instructions written to $EXPORT_DIR/IMPORT_INSTRUCTIONS.md"
echo ""
echo "=== Export Complete ==="
echo "Package: $EXPORT_DIR"
echo "Skills: $SKILL_COUNT"
echo ""
echo "To transfer to PC:"
echo "  scp -r $EXPORT_DIR user@pc:/tmp/"
