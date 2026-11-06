#!/bin/sh
set -eu

echo "== identity =="
id
uname -a

echo "== kernel knobs =="
for f in /proc/sys/user/max_user_namespaces /proc/sys/kernel/unprivileged_userns_clone; do
  if [ -r "$f" ]; then echo "$f=$(cat "$f")"; else echo "$f=<absent>"; fi
done

echo "== bwrap =="
bwrap --version

WORK=/tmp/fx-smoke-work
SESSION=/tmp/fx-smoke-session
rm -rf "$WORK" "$SESSION"
mkdir -p "$WORK" "$SESSION"

cat >"$WORK/check.py" <<'PY'
import os, socket, sys
print("cwd", os.getcwd())
print("mnt_exists", os.path.exists("/mnt"))
print("proc_exists", os.path.exists("/proc"))
print("token_env", "INTERNAL_SERVICE_TOKEN" in os.environ)
s = socket.socket()
s.settimeout(1)
print("net_connect_ex", s.connect_ex(("1.1.1.1", 80)))
PY

BWRAP="bwrap --unshare-all --die-with-parent --clearenv \
  --setenv PATH /opt/python3.12/bin:/usr/local/bin:/usr/bin:/bin \
  --setenv HOME /tmp \
  --ro-bind /usr /usr --ro-bind /lib /lib --ro-bind-try /lib64 /lib64 \
  --ro-bind /etc /etc --ro-bind /opt /opt --dev /dev \
  --size 67108864 --tmpfs /tmp \
  --bind $WORK /work --chdir /work"

echo "== python in sandbox =="
$BWRAP python /work/check.py

echo "== node in sandbox =="
echo 'console.log("node_ok", process.cwd())' >"$WORK/check.js"
$BWRAP node /work/check.js

echo "== network shared =="
bwrap --unshare-all --share-net --die-with-parent --clearenv \
  --setenv PATH /opt/python3.12/bin:/usr/local/bin:/usr/bin:/bin \
  --ro-bind /usr /usr --ro-bind /lib /lib --ro-bind-try /lib64 /lib64 \
  --ro-bind /etc /etc --ro-bind /opt /opt --dev /dev \
  --size 67108864 --tmpfs /tmp --bind "$WORK" /work --chdir /work \
  python -c 'import socket; s=socket.socket(); s.settimeout(1); print("net_shared", s.connect_ex(("1.1.1.1", 80)))'

echo "== unix socket inside sandbox =="
cat >"$SESSION/server.py" <<'PY'
import os, socket, subprocess, threading
path = "/tmp/fx-smoke-session/session.sock"
try: os.unlink(path)
except FileNotFoundError: pass
srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
srv.bind(path); srv.listen(1)
def serve():
    c, _ = srv.accept()
    c.sendall(b'{"session":"ok"}\n')
    c.close()
threading.Thread(target=serve, daemon=True).start()
open("/tmp/fx-smoke-session/client.py", "w").write(
    "import os,socket; s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); "
    "s.connect(os.environ['MUAD_SESSION_SOCKET']); print(s.recv(1024).decode())"
)
cmd = ("bwrap --unshare-all --die-with-parent --clearenv "
       "--setenv PATH /opt/python3.12/bin:/usr/local/bin:/usr/bin:/bin "
       "--setenv MUAD_SESSION_SOCKET " + path + " "
       "--ro-bind /usr /usr --ro-bind /lib /lib --ro-bind-try /lib64 /lib64 "
       "--ro-bind /etc /etc --ro-bind /opt /opt --dev /dev "
       "--size 67108864 --tmpfs /tmp "
       "--bind /tmp/fx-smoke-session /tmp/fx-smoke-session "
       "--bind /tmp/fx-smoke-work /work --chdir /work "
       "python /tmp/fx-smoke-session/client.py")
r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
print("rc", r.returncode)
print("out", r.stdout.strip())
print("err", r.stderr.strip())
raise SystemExit(0 if r.returncode == 0 else 1)
PY
python "$SESSION/server.py"

echo "== done =="
