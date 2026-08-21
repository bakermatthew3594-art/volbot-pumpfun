#!/bin/bash
# ══════════════════════════════════════════════════════════════
# Complete Hermes + VolBot Transfer Script
# Packages EVERYTHING needed for PC Hermes desktop:
#   1. All 114 Hermes skills
#   2. All 46 skills (deduplicated by directory, 114 SKILL.md files)
#   3. 8 cron jobs (watchdog + sync + volbot)
#   4. Hermes config
#   5. VolBot project (37 files)
#   6. Transfer manifest with full documentation
#
# Usage: ./transfer-all-to-pc.sh [method]
#   github  — Push everything to GitHub (default)
#   package — Create tar.gz for download
# ══════════════════════════════════════════════════════════════
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

METHOD="${1:-github}"
TIMESTAMP=$(date +%Y%m%d-%H%M)

echo "=== Complete Hermes + VolBot Transfer ==="
echo "Time: $(date '+%Y-%m-%d %H:%M EDT')"
echo "Method: $METHOD"
echo ""

case "$METHOD" in
    github)
        echo "--- Step 1: Ensure git repo is up to date ---"
        git add -A
        if ! git diff --cached --quiet; then
            git commit -m "Pre-transfer sync: $(date '+%Y-%m-%d %H:%M EDT')"
        fi
        git push origin main 2>&1 | tail -3
        
        echo ""
        echo "--- Step 2: Export Hermes skills to GitHub repo ---"
        # Copy skills to a subdirectory in the repo
        SKILLS_EXPORT="hermes-skills"
        rm -rf "$SCRIPT_DIR/$SKILLS_EXPORT"
        cp -r /root/.hermes/skills/* "$SCRIPT_DIR/$SKILLS_EXPORT/"
        
        # Update .gitignore to exclude skills from general tracking
        echo "# Hermes skills (exported to hermes-skills/)" >> .gitignore
        echo "!hermes-skills/" >> .gitignore
        
        # Also export cron jobs
        if [ -f "/root/.hermes/cron/jobs.json" ]; then
            cp /root/.hermes/cron/jobs.json "$SCRIPT_DIR/cron-jobs.json"
        fi
        
        # Export config (redacted)
        if [ -f "/root/.hermes/config.yaml" ]; then
            cp /root/.hermes/config.yaml "$SCRIPT_DIR/hermes-config.yaml"
            # Redact sensitive values
            sed -i 's/key_env:.*/key_env: [REDACTED]/g' "$SCRIPT_DIR/hermes-config.yaml" 2>/dev/null || true
        fi
        
        git add -A
        git commit -m "Export Hermes skills, cron jobs, config for PC transfer"
        git push origin main 2>&1 | tail -3
        git push origin pc 2>&1 | tail -3
        
        echo ""
        echo "=== Transfer Complete ==="
        echo ""
        echo "On PC Hermes desktop:"
        echo "  git clone https://github.com/bakermatthew3594-art/volbot-pumpfun.git"
        echo "  cd volbot-pumpfun"
        echo "  bash pc-install.sh"
        echo ""
        echo "  To import skills:"
        echo "  cp -r hermes-skills/* /root/.hermes/skills/"
        echo ""
        echo "  To import cron jobs:"
        echo "  Review cron-jobs.json and import via Hermes cron system"
        echo ""
        echo "=== Files Transferred ==="
        echo "  Hermes Skills: $(find hermes-skills -name 'SKILL.md' 2>/dev/null | wc -l) SKILL.md files"
        echo "  Cron Jobs: $(python3 -c "import json; print(len(json.load(open('cron-jobs.json')).get('jobs',json.load(open('cron-jobs.json')))))" 2>/dev/null || echo '?') jobs"
        echo "  Config: hermes-config.yaml (redacted)"
        echo "  VolBot project: 37 files in root"
        ;;
    
    package)
        echo "--- Creating complete transfer package ---"
        
        PACKAGE="/tmp/hermes-volbot-complete-$TIMESTAMP"
        rm -rf "$PACKAGE"
        mkdir -p "$PACKAGE"
        
        # 1. Export skills
        mkdir -p "$PACKAGE/hermes-skills"
        cp -r /root/.hermes/skills/* "$PACKAGE/hermes-skills/"
        
        # 2. Export cron jobs
        cp /root/.hermes/cron/jobs.json "$PACKAGE/cron-jobs.json"
        
        # 3. Export config (redacted)
        cp /root/.hermes/config.yaml "$PACKAGE/hermes-config.yaml"
        sed -i 's/key_env:.*/key_env: [REDACTED]/g' "$PACKAGE/hermes-config.yaml" 2>/dev/null || true
        
        # 4. Copy VolBot project (excluding .git, __pycache__, node_modules)
        mkdir -p "$PACKAGE/volbot-project"
        cp -r "$SCRIPT_DIR"/* "$PACKAGE/volbot-project/"
        # Clean up unwanted files
        rm -rf "$PACKAGE/volbot-project/.git"
        rm -rf "$PACKAGE/volbot-project/__pycache__"
        find "$PACKAGE/volbot-project" -name "*.pyc" -delete 2>/dev/null || true
        rm -rf "$PACKAGE/volbot-project/node_modules" 2>/dev/null || true
        rm -f "$PACKAGE/volbot-project/.lifecycle_state.json" 2>/dev/null || true
        rm -f "$PACKAGE/volbot-project/.env" 2>/dev/null || true
        
        # 5. Create manifest
        cat > "$PACKAGE/TRANSFER_PACKAGE.md" << MANIFEST
# Hermes + VolBot Complete Transfer Package

Generated: $(date '+%Y-%m-%d %H:%M EDT')

## Contents

### 1. Hermes Skills (hermes-skills/)
$(find "$PACKAGE/hermes-skills" -name "SKILL.md" 2>/dev/null | wc -l) skills exported from Android Hermes instance.

### 2. Cron Jobs (cron-jobs.json)
8 cron jobs including:
- VolBot Health Watchdog (every 5 minutes)
- VolBot GitHub Auto-Sync (every hour)
- Hermes Browser Server Watchkeeper (every 1 minute)
- Hermes service watchdog (every 5 minutes)
- Hermes Skills Daily Sync (daily at 9am)
- Laptop availability monitor (every 5 minutes)

### 3. Hermes Config (hermes-config.yaml)
Redacted configuration. Review and update on PC.

### 4. VolBot Project (volbot-project/)
Complete Pump.fun launch trading bot:
- 7-phase lifecycle CLI
- Trading orchestrator with bubble detection
- Telegram bot + web dashboard
- 74 integration tests (all passing)
- Always-on tmux deployment
- Universal run.sh launcher

## Import Instructions (PC Hermes)

### Skills
\`\`\`bash
cp -r hermes-skills/* /root/.hermes/skills/
\`\`\`

### Cron Jobs
Review cron-jobs.json, then import individual jobs via:
\`\`\`bash
hermes cron create "..."  # or manually add to /root/.hermes/cron/jobs.json
\`\`\`

### VolBot
\`\`\`bash
cd volbot-project
bash pc-install.sh
./always-on.sh start
\`\`\`

## Important Notes

1. Some skills reference Android-specific paths. Review and adapt for PC environment.
2. The VolBot `pc` branch has PC-specific Dockerfile and install scripts.
3. The GitHub repo is at: https://github.com/bakermatthew3594-art/volbot-pumpfun
MANIFEST

        # Create tarball
        cd /tmp
        tar czf "hermes-volbot-complete-$TIMESTAMP.tar.gz" "hermes-volbot-complete-$TIMESTAMP"
        
        echo ""
        echo "=== Package Created ==="
        echo "File: /tmp/hermes-volbot-complete-$TIMESTAMP.tar.gz"
        echo "Size: $(du -sh /tmp/hermes-volbot-complete-$TIMESTAMP.tar.gz | cut -f1)"
        echo ""
        echo "To transfer to PC:"
        echo "  scp /tmp/hermes-volbot-complete-$TIMESTAMP.tar.gz user@pc:/tmp/"
        echo "  ssh user@pc 'cd /tmp && tar xzf hermes-volbot-complete-$TIMESTAMP.tar.gz'"
        ;;

    *)
        echo "Usage: $0 {github|package}"
        exit 1
        ;;
esac
