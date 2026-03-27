# Harbinger Capital Engine — Linux VPS Migration Checklist
# Every command. Nothing assumed. Run as root unless stated.

---

## 0. CHOOSE A VPS

Minimum spec: 2 vCPU, 4 GB RAM, 40 GB SSD, Ubuntu 24.04 LTS
Recommended providers: Hetzner CX22 (€4.51/mo), DigitalOcean Basic (£6/mo), Vultr Regular (£5/mo)

Hetzner fastest to provision:
  https://console.hetzner.cloud → New Project → Add Server → Ubuntu 24.04 → CX22

Note your server IP. Add your SSH public key during provisioning.

---

## 1. INITIAL SERVER ACCESS

```bash
ssh root@YOUR_SERVER_IP
```

If you need to generate an SSH key on your Mac first:
```bash
ssh-keygen -t ed25519 -C "harbinger-vps"
cat ~/.ssh/id_ed25519.pub   # paste this into Hetzner during server creation
```

---

## 2. SYSTEM UPDATE AND BASE PACKAGES

```bash
apt update && apt upgrade -y
apt install -y \
  git curl wget unzip \
  python3 python3-pip python3-venv python3-dev \
  ffmpeg exiftool \
  build-essential libssl-dev libffi-dev \
  cron logrotate \
  htop tmux nano
```

Verify FFmpeg is installed and has libx264 + libass:
```bash
ffmpeg -version | head -3
ffmpeg -buildconf | grep -E "libx264|libass"
```

Both must appear. If libass is missing:
```bash
apt install -y libass-dev
# Then rebuild ffmpeg or install from a PPA that includes libass:
add-apt-repository ppa:savoury1/ffmpeg4
apt update && apt install -y ffmpeg
```

Verify ExifTool:
```bash
exiftool -ver
```

---

## 3. CREATE PROJECT DIRECTORY AND USER (OPTIONAL)

Run as root is fine for a single-purpose VPS. If you want a dedicated user:
```bash
useradd -m -s /bin/bash harbinger
su - harbinger
```

Otherwise stay as root and use /root/capital-engine as the base.

---

## 4. TRANSFER THE CODEBASE

**Option A — rsync from your Mac (recommended):**
Run this on your Mac, not the server:
```bash
rsync -avz --exclude='.git' --exclude='output/' --exclude='logs/*.log' \
  /Users/kolly/capital-engine/ root@YOUR_SERVER_IP:/root/capital-engine/
```

**Option B — Git (if you have a private repo):**
```bash
# On server:
git clone git@github.com:YOUR_USERNAME/capital-engine.git /root/capital-engine
```

Verify transfer:
```bash
ls /root/capital-engine/scripts/agents/
# Should show: cultural_radar.py  creative_synthesis.py  production_agent.py  quality_mirror.py  algorithm_intelligence.py
```

---

## 5. PYTHON VIRTUAL ENVIRONMENT

```bash
cd /root/capital-engine
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

If requirements.txt doesn't exist, install manually:
```bash
pip install \
  requests \
  anthropic \
  fal-client \
  google-api-python-client \
  google-auth-httplib2 \
  google-auth-oauthlib \
  runwayml \
  cloudinary \
  python-dotenv
```

Verify key imports:
```bash
python3 -c "import requests, anthropic, fal_client; print('OK')"
```

---

## 6. INSTALL THE MONTSERRAT FONT

The caption engine uses Montserrat. On Linux there is no ~/Library/Fonts.
```bash
mkdir -p /usr/local/share/fonts/truetype
# Transfer from your Mac:
# Run on Mac:
scp "/Users/kolly/Library/Fonts/Montserrat[wght].ttf" root@YOUR_SERVER_IP:/usr/local/share/fonts/truetype/
# Then on server:
fc-cache -fv
fc-list | grep -i montserrat   # should appear
```

Update the font path in production_agent.py. It is hardcoded on line 589:
```bash
grep -n "FONTS_DIR" /root/capital-engine/scripts/agents/production_agent.py
```

Edit the line to:
```python
FONTS_DIR = "/usr/local/share/fonts/truetype"
```

---

## 7. ENVIRONMENT VARIABLES (.env)

Transfer the .env file from your Mac:
```bash
# Run on Mac:
scp /Users/kolly/capital-engine/.env root@YOUR_SERVER_IP:/root/capital-engine/.env
```

Lock file permissions immediately:
```bash
chmod 600 /root/capital-engine/.env
```

Verify all keys present:
```bash
grep -E "^[A-Z_]+=." /root/capital-engine/.env | cut -d= -f1
```

Expected keys:
  ELEVENLABS_API_KEY
  FAL_KEY
  PIAPI_KEY
  BUFFER_API_TOKEN
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID
  YOUTUBE_API_KEY
  CLOUDINARY_API_KEY / CLOUDINARY_URL
  ANTHROPIC_API_KEY
  REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET
  TWITTER_BEARER_TOKEN
  RUNWAYML_API_SECRET

---

## 8. FIX ALL HARDCODED MAC PATHS

Search for any remaining Mac-specific paths:
```bash
grep -rn "/Users/kolly" /root/capital-engine/scripts/ 2>/dev/null
```

The known hardcoded path is FONTS_DIR in production_agent.py (fixed in step 6).
If grep finds others, edit them to /root/capital-engine/... equivalents.

Also update BASE_DIR resolution — all agents use Path(__file__).parent.parent.parent
which resolves correctly relative to the script location. No change needed there.

---

## 9. DIRECTORY STRUCTURE

Create output and logs dirs (rsync may have created them, verify):
```bash
mkdir -p /root/capital-engine/{output,logs,assets}
chmod 755 /root/capital-engine/{output,logs,assets}
```

---

## 10. TEST PYTHON PATH

```bash
cd /root/capital-engine
source venv/bin/activate
python3 -c "
from pathlib import Path
import sys
sys.path.insert(0, 'scripts')
import prompt_engine
import caption_engine
print('prompt_engine OK')
print('caption_engine OK')
"
```

---

## 11. RUN HEALTH CHECK

```bash
cd /root/capital-engine
source venv/bin/activate
source .env
python3 scripts/harbinger_core.py --health
```

All keys should show ✅ SET. fal-client should show ✅ INSTALLED.
Fix any ❌ before proceeding.

---

## 12. RUN ONE FULL MANUAL CYCLE (slot 1 — review before distribute)

```bash
cd /root/capital-engine
source venv/bin/activate
source .env

# First: run the intelligence agents
python3 scripts/agents/cultural_radar.py
python3 scripts/agents/algorithm_intelligence.py
python3 scripts/agents/creative_synthesis.py --slot 1

# Then: production + micro-loop (halted before distribute for review)
python3 scripts/harbinger_core.py --manual-cycle 1
```

Verify output:
```bash
ls -lh output/post_*slot1*.mp4
# Check: file exists, size > 0.5MB
ffprobe -v quiet -show_entries stream=width,height,duration -of csv=p=0 output/post_*slot1*.mp4
# Expected: 1080,1920,<duration>
```

If everything looks correct, run distribute manually:
```bash
python3 scripts/archive/distribute.py --slot 1
```

---

## 13. INSTALL CRON

Edit the HARBINGER path in crontab.txt first — it is set to /root/capital-engine:
```bash
head -10 /root/capital-engine/scripts/crontab.txt
# Confirm HARBINGER=/root/capital-engine is correct
```

Also update the venv: cron does not activate virtualenvs automatically.
Replace `python3` with the venv path in crontab.txt:
```bash
sed -i 's|python3 scripts|/root/capital-engine/venv/bin/python3 scripts|g' \
  /root/capital-engine/scripts/crontab.txt
```

Install:
```bash
crontab /root/capital-engine/scripts/crontab.txt
crontab -l    # verify it looks correct
```

Ensure cron daemon is running:
```bash
systemctl status cron
systemctl enable cron   # starts on reboot
```

---

## 14. CONFIGURE LOGROTATE

Prevent logs growing unbounded:
```bash
cat > /etc/logrotate.d/harbinger << 'EOF'
/root/capital-engine/logs/*.log {
    daily
    rotate 14
    compress
    missingok
    notifempty
    copytruncate
}
EOF
```

Test:
```bash
logrotate --debug /etc/logrotate.d/harbinger
```

---

## 15. FIREWALL (UFW)

```bash
ufw allow OpenSSH
ufw enable
ufw status
```

No inbound ports needed — all Harbinger traffic is outbound (API calls).

---

## 16. KEEP-ALIVE ON REBOOT (systemd override for cron)

Cron already starts on boot if enabled (step 13). Optionally add a systemd service
so the first slot 1 cycle runs on first boot automatically:

```bash
cat > /etc/systemd/system/harbinger-boot.service << 'EOF'
[Unit]
Description=Harbinger Capital Engine boot check
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=root
WorkingDirectory=/root/capital-engine
EnvironmentFile=/root/capital-engine/.env
ExecStart=/root/capital-engine/venv/bin/python3 scripts/harbinger_core.py --health
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable harbinger-boot
```

---

## 17. TELEGRAM ALERT TEST

```bash
cd /root/capital-engine
source .env
python3 -c "
import os, requests
token = os.environ.get('TELEGRAM_BOT_TOKEN')
chat_id = os.environ.get('TELEGRAM_CHAT_ID')
r = requests.post(
    f'https://api.telegram.org/bot{token}/sendMessage',
    json={'chat_id': chat_id, 'text': '✅ Harbinger VPS online and operational.'}
)
print(r.status_code, r.json().get('ok'))
"
```

Expected: `200 True`

---

## 18. OUTPUT STORAGE — OPTIONAL CLOUDINARY SYNC

Videos in output/ will accumulate. On a 40GB VPS, 1.5MB per video × 5/day × 30 days = ~225MB.
Manageable. But if you want remote backup:

```bash
pip install cloudinary
python3 -c "
import cloudinary, cloudinary.uploader, os
cloudinary.config(cloudinary_url=os.environ.get('CLOUDINARY_URL'))
r = cloudinary.uploader.upload('output/post_test.mp4', resource_type='video')
print(r.get('secure_url'))
"
```

Add Cloudinary upload call to distribute.py if you want permanent video URLs.

---

## 19. MONITORING — OPTIONAL

Install a lightweight process monitor:
```bash
apt install -y monit

cat > /etc/monit/conf.d/harbinger << 'EOF'
check file harbinger_cron_log with path /root/capital-engine/logs/cron.log
  if timestamp > 7 hours then alert

check filesystem rootfs with path /
  if space usage > 85% then alert
EOF

systemctl restart monit
```

---

## 20. DECOMMISSION MAC

Once VPS is confirmed stable for 48h:

1. Verify 5 videos posted on all platforms
2. Verify Telegram alerts arriving correctly
3. Verify nightly quality mirror ran (check logs/delta_report_*.json exists)
4. Stop Mac cron: `crontab -r` on your Mac
5. Archive Mac output/ dir: `tar -czf harbinger_mac_backup_$(date +%Y%m%d).tar.gz output/ logs/`
6. Transfer backup to VPS or cloud storage

---

## QUICK REFERENCE — COMMON COMMANDS ON VPS

```bash
# Watch live cron output
tail -f /root/capital-engine/logs/cron.log

# Check today's production
ls -lh /root/capital-engine/output/post_$(date +%Y%m%d)*.mp4

# Run a manual slot
cd /root/capital-engine && source venv/bin/activate && source .env
python3 scripts/harbinger_core.py --manual-cycle 1

# View quality mirror delta
cat /root/capital-engine/logs/delta_report_$(date +%Y-%m-%d).json | python3 -m json.tool

# Health check
python3 scripts/harbinger_core.py --health

# View all cron jobs
crontab -l

# Restart cron after editing
crontab /root/capital-engine/scripts/crontab.txt
```

---

## ESTIMATED MIGRATION TIME

| Step | Time |
|---|---|
| Server provisioning | 2 min |
| System packages | 5 min |
| Codebase transfer | 2 min |
| Python env + deps | 5 min |
| Font + path fixes | 5 min |
| .env transfer + verify | 3 min |
| Health check | 2 min |
| Manual cycle test | 10–15 min (Kling render time) |
| Cron install + verify | 2 min |
| Telegram test | 1 min |
| **Total** | **~35–40 min** |
