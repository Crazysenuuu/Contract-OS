"""Double-fork daemonizer: run a command detached from the calling shell.

Usage: python3 daemonize.py <logfile> <command> [args...]
"""
import os
import sys


def daemonize(command, logfile):
    pid = os.fork()
    if pid > 0:
        os.waitpid(pid, 0)  # reap first child
        return
    os.setsid()
    pid = os.fork()
    if pid > 0:
        os._exit(0)
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, 0)
    fd = os.open(logfile, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.dup2(fd, 1)
    os.dup2(fd, 2)
    os.execvp(command[0], command)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: daemonize.py <logfile> <cmd...>", file=sys.stderr)
        sys.exit(2)
    daemonize(sys.argv[2:], sys.argv[1])
