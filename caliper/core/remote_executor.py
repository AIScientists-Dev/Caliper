"""RemoteExecutor — run a code step on a remote compute host over SSH.

Mirrors core.executor.Executor, but the step runs on a remote machine (the lab server)
inside a confined workspace there: reads inputs in place, writes only under the
workspace, and STREAMS stdout back as it runs. This is how the EC2 "brain" dispatches
CPU work to the lab server. Each run uses its own SSH connection, so many requests can
run simultaneously (like multiple terminals).
"""
from __future__ import annotations

import json
import os
import posixpath
import shlex
import time
from typing import Callable, List, Optional

from .executor import ExecResult, check_code


class RemoteExecutor:
    def __init__(self, host: str, user: str, key_filename: Optional[str] = None,
                 password: Optional[str] = None, port: int = 22,
                 workspace: str = ".", readonly_inputs: Optional[List[str]] = None,
                 python: str = "python3", path_prepend: str = "", timeout: int = 1800,
                 bwrap: str = ""):
        self.host = host
        self.user = user
        self.key_filename = key_filename
        self.password = password
        self.port = port
        self.workspace = workspace
        self.readonly_inputs = list(readonly_inputs or [])
        self.python = python
        self.path_prepend = path_prepend
        self.timeout = timeout
        self.bwrap = bwrap  # path to bubblewrap on the remote; OS-confines writes to workspace

    def _connect(self):
        import paramiko
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        c.connect(self.host, port=self.port, username=self.user,
                  key_filename=self.key_filename, password=self.password, timeout=30)
        return c

    def run(self, code: str, inputs: Optional[List[dict]] = None,
            on_output: Optional[Callable[[str], None]] = None) -> ExecResult:
        readonly = set(self.readonly_inputs)
        for f in (inputs or []):
            if f.get("path"):
                readonly.add(f["path"])
        violations = check_code(code, self.workspace, readonly)
        if violations:
            return ExecResult(False, "", "BLOCKED by workspace policy: " + "; ".join(violations),
                              -3, blocked=True)

        c = self._connect()
        try:
            run_id = "run_" + os.urandom(4).hex()
            rundir = posixpath.join(self.workspace, ".caliper_runs", run_id)
            _, so, _ = c.exec_command("mkdir -p " + shlex.quote(rundir))
            so.channel.recv_exit_status()

            sftp = c.open_sftp()
            with sftp.open(posixpath.join(rundir, "step.py"), "w") as f:
                f.write(code)
            sftp.close()

            env = (f"CALIPER_INPUTS={shlex.quote(json.dumps(inputs or []))} "
                   f"CALIPER_WORKSPACE={shlex.quote(self.workspace)} "
                   f"TMPDIR={shlex.quote(rundir)} ")
            path = f"PATH={self.path_prepend}:$PATH " if self.path_prepend else ""
            inner = f"{path}{env}{shlex.quote(self.python)} step.py"
            if self.bwrap:
                # whole filesystem read-only EXCEPT the workspace + /tmp -> writes
                # outside the workspace fail at the OS level, however the code tries.
                ws = shlex.quote(self.workspace)
                cmd = (f"{shlex.quote(self.bwrap)} --ro-bind / / --dev /dev --proc /proc "
                       f"--tmpfs /tmp --bind {ws} {ws} --chdir {shlex.quote(rundir)} "
                       f"-- /bin/sh -c {shlex.quote(inner)}")
            else:
                cmd = f"cd {shlex.quote(rundir)} && {inner}"

            chan = c.get_transport().open_session()
            chan.settimeout(self.timeout)
            chan.exec_command(cmd)
            chan.setblocking(False)

            out, err = [], []
            deadline = time.time() + self.timeout
            while True:
                progressed = False
                while chan.recv_ready():
                    d = chan.recv(8192).decode(errors="replace")
                    out.append(d)
                    progressed = True
                    if on_output and d:
                        on_output(d)
                while chan.recv_stderr_ready():
                    err.append(chan.recv_stderr(8192).decode(errors="replace"))
                    progressed = True
                if chan.exit_status_ready() and not chan.recv_ready() and not chan.recv_stderr_ready():
                    break
                if time.time() > deadline:
                    return ExecResult(False, "".join(out), f"timeout after {self.timeout}s", -1)
                if not progressed:
                    time.sleep(0.05)
            rc = chan.recv_exit_status()
            return ExecResult(rc == 0, "".join(out), "".join(err), rc)
        finally:
            c.close()

    def provision(self, install_cmd: str, on_output: Optional[Callable[[str], None]] = None) -> ExecResult:
        """Run a vetted install command (from the pack allow-list) on the remote host.

        Used by the self-evolve loop to add a missing pack tool in-session. Installs go
        into the remote workspace env; the command must come from Pack.install_command.
        """
        path = f"PATH={self.path_prepend}:$PATH " if self.path_prepend else ""
        c = self._connect()
        try:
            chan = c.get_transport().open_session()
            chan.settimeout(self.timeout)
            chan.exec_command(f"cd {shlex.quote(self.workspace)} && {path}{install_cmd}")
            out = []
            chan.setblocking(True)
            f = chan.makefile()
            for line in f:
                out.append(line)
                if on_output:
                    on_output(line)
            rc = chan.recv_exit_status()
            return ExecResult(rc == 0, "".join(out), "", rc)
        finally:
            c.close()
