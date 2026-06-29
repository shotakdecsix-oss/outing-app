"""お出かけアプリ — 変更をcommit＆push"""
import re, subprocess, sys, os

CONFIG_PATH = r"C:\Users\Shoichi\Desktop\wc2026\wc2026_config.json"
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
REPO_NAME   = "outing-app"
GITHUB_USER = "shotakdecsix-oss"
LOG_PATH    = os.path.join(SCRIPT_DIR, "push_log.txt")

with open(CONFIG_PATH, "rb") as f:
    raw = f.read().decode("latin-1")
m = re.search(r'"github_token":\s*"([^"]+)"', raw)
TOKEN = m.group(1)

for p in [r"C:\Program Files\Git\bin\git.exe",
          r"C:\Program Files (x86)\Git\bin\git.exe", "git"]:
    try:
        if subprocess.run([p, "--version"], capture_output=True).returncode == 0:
            GIT = p
            break
    except Exception:
        pass

log_lines = []
def log(msg):
    print(msg)
    log_lines.append(msg)

for lock in ["index.lock", "HEAD.lock"]:
    lpath = os.path.join(SCRIPT_DIR, ".git", lock)
    if os.path.exists(lpath):
        try:
            os.remove(lpath)
            log(f"[OK] ロック解除: {lock}")
        except Exception as e:
            log(f"[WARN] {e}")

def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=SCRIPT_DIR)
    out = (r.stdout + r.stderr).strip()
    if out: log(out)
    return r

REMOTE = f"https://{TOKEN}@github.com/{GITHUB_USER}/{REPO_NAME}.git"
run([GIT, "remote", "set-url", "origin", REMOTE])

log("\n--- git add & commit ---")
run([GIT, "add", "app.py", "index.html"])
run([GIT, "commit", "-m", "update: お出かけアプリ"])

log("\n--- git push ---")
result = run([GIT, "push", "origin", "main"])
log(f"\nreturncode: {result.returncode}")

with open(LOG_PATH, "w", encoding="utf-8") as f:
    f.write("\n".join(log_lines))

input("\nEnterで終了（push_log.txtにも記録済み）")
