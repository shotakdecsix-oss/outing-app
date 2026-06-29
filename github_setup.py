"""
お出かけアプリ — 初回GitHub設定＆push
wc2026_config.json からトークンを読む
"""
import re, subprocess, sys, os, json, urllib.request, urllib.error

CONFIG_PATH = r"C:\Users\Shoichi\Desktop\wc2026\wc2026_config.json"
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
REPO_NAME   = "outing-app"
GITHUB_USER = "shotakdecsix-oss"

# トークン取得
with open(CONFIG_PATH, "rb") as f:
    raw = f.read().decode("latin-1")
m = re.search(r'"github_token":\s*"([^"]+)"', raw)
if not m:
    print("[ERROR] github_token が見つかりません")
    input("Enterで終了")
    sys.exit(1)
TOKEN = m.group(1)
print(f"[OK] トークン取得済み")

# git コマンドを探す
for p in [r"C:\Program Files\Git\bin\git.exe",
          r"C:\Program Files (x86)\Git\bin\git.exe", "git"]:
    try:
        if subprocess.run([p, "--version"], capture_output=True).returncode == 0:
            GIT = p
            break
    except Exception:
        pass
else:
    print("[ERROR] git が見つかりません")
    input("Enterで終了")
    sys.exit(1)
print(f"[OK] git: {GIT}")

def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=SCRIPT_DIR)
    out = (r.stdout + r.stderr).strip()
    if out: print(out)
    return r

# GitHub リポジトリ作成
print(f"\n--- GitHub リポジトリ作成: {REPO_NAME} ---")
api_url = "https://api.github.com/user/repos"
payload = json.dumps({"name": REPO_NAME, "private": False}).encode()
req = urllib.request.Request(api_url, data=payload, method="POST")
req.add_header("Authorization", f"token {TOKEN}")
req.add_header("Content-Type", "application/json")
try:
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
        print(f"[OK] リポジトリ作成: {data['html_url']}")
except urllib.error.HTTPError as e:
    body = e.read().decode()
    if "already exists" in body or "name already exists" in body:
        print("[INFO] リポジトリは既に存在します（続行）")
    else:
        print(f"[ERROR] {e.code}: {body}")
        input("Enterで終了")
        sys.exit(1)

REMOTE = f"https://{TOKEN}@github.com/{GITHUB_USER}/{REPO_NAME}.git"

# git 初期化
print("\n--- git init ---")
if not os.path.exists(os.path.join(SCRIPT_DIR, ".git")):
    run([GIT, "init"])
    run([GIT, "checkout", "-b", "main"])
else:
    print("[INFO] git already initialized")

# ロックファイル削除
for lock in ["index.lock", "HEAD.lock"]:
    lpath = os.path.join(SCRIPT_DIR, ".git", lock)
    if os.path.exists(lpath):
        try:
            os.remove(lpath)
            print(f"[OK] ロック解除: {lock}")
        except Exception as e:
            print(f"[WARN] {e}")

run([GIT, "remote", "remove", "origin"])
run([GIT, "remote", "add", "origin", REMOTE])

print("\n--- add & commit ---")
run([GIT, "add", "."])
run([GIT, "commit", "-m", "first commit: お出かけアプリ"])

print("\n--- push ---")
r = run([GIT, "push", "-u", "origin", "main"])
if r.returncode == 0:
    print(f"\n[完了] https://github.com/{GITHUB_USER}/{REPO_NAME}")
else:
    print("\n[ERROR] pushに失敗しました")

input("\nEnterで終了")
