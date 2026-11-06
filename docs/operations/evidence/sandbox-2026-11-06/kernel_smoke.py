import ctypes
import os
import socket
import sys
import tempfile

libc = ctypes.CDLL(None, use_errno=True)


def _write(fd: int, text: str) -> None:
    os.write(fd, text.encode())


def _read(fd: int) -> str:
    return os.read(fd, 4096).decode()


def mount_tmpfs(path: str) -> None:
    if libc.mount(b"tmpfs", path.encode(), b"tmpfs", 0, b"size=1m") != 0:
        raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()))


def umount(path: str) -> None:
    libc.umount2(path.encode(), 0)


def child(ready_w: int, go_r: int) -> int:
    try:
        os.unshare(os.CLONE_NEWUSER)
    except OSError as exc:
        _write(ready_w, f"FAIL unshare-user {exc.errno} {exc.strerror}\n")
        return 1
    _write(ready_w, "OK user\n")
    if _read(go_r) != "go":
        return 1
    # After the parent writes the uid/gid maps, this process is uid 0 inside
    # the user namespace and has CAP_SYS_ADMIN there.
    try:
        os.unshare(
            os.CLONE_NEWNS
            | os.CLONE_NEWPID
            | os.CLONE_NEWNET
            | os.CLONE_NEWIPC
            | os.CLONE_NEWUTS
        )
    except OSError as exc:
        _write(ready_w, f"FAIL unshare-others {exc.errno} {exc.strerror}\n")
        return 1
    _write(ready_w, "OK namespaces\n")

    with tempfile.TemporaryDirectory() as tmp:
        target = os.path.join(tmp, "mnt")
        os.mkdir(target)
        try:
            mount_tmpfs(target)
            umount(target)
            _write(ready_w, "OK mount\n")
        except OSError as exc:
            _write(ready_w, f"FAIL mount {exc.errno} {exc.strerror}\n")
            return 1

    pid = os.fork()
    if pid == 0:
        ok = os.getpid() == 1
        _write(ready_w, f"{'OK' if ok else 'FAIL'} pid {os.getpid()}\n")
        os._exit(0 if ok else 1)
    _, status = os.waitpid(pid, 0)
    if status != 0:
        return 1

    s = socket.socket()
    s.settimeout(1)
    code = s.connect_ex(("1.1.1.1", 80))
    _write(ready_w, f"{'OK' if code != 0 else 'FAIL'} net {code}\n")
    return 0 if code != 0 else 1


def main() -> int:
    print("uid:", os.getuid(), "gid:", os.getgid())
    print("kernel:", os.uname().release)
    for path in ("/proc/sys/user/max_user_namespaces", "/proc/sys/kernel/unprivileged_userns_clone"):
        try:
            print(f"{path}={open(path).read().strip()}")
        except OSError as exc:
            print(f"{path}=<{exc.strerror}>")

    ready_r, ready_w = os.pipe()
    go_r, go_w = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(ready_r)
        os.close(go_w)
        os._exit(child(ready_w, go_r))
    os.close(ready_w)
    os.close(go_r)

    first = _read(ready_r)
    print(first.strip())
    if not first.startswith("OK user"):
        os.kill(pid, 9)
        os.waitpid(pid, 0)
        return 1

    uid, gid = os.getuid(), os.getgid()
    for name, value in (
        ("setgroups", "deny\n"),
        ("uid_map", f"0 {uid} 1\n"),
        ("gid_map", f"0 {gid} 1\n"),
    ):
        with open(f"/proc/{pid}/{name}", "w") as stream:
            stream.write(value)
    _write(go_w, "go")
    os.close(go_w)

    ok = True
    while True:
        line = _read(ready_r)
        if not line:
            break
        print(line.strip())
        if not line.startswith("OK"):
            ok = False
    os.close(ready_r)
    _, status = os.waitpid(pid, 0)
    print("RESULT:", "PASS" if ok and status == 0 else "FAIL")
    return 0 if ok and status == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
