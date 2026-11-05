"""Official Console controller-v2 sessions inside an operator-owned container.

Uses the real management assignment API and the shared-host JSON-line control
protocol. It never launches a replacement Codex CLI or changes agent definitions.
"""

import asyncio
import json
import os
import re
import stat
from pathlib import Path
from uuid import UUID

import httpx

from .engineering import ConsoleCodex

_CONTROL = r"""
const fs=require('node:fs'),net=require('node:net');
const [file,request]=process.argv.slice(1);
const info=fs.lstatSync(file);
if(!info.isFile()||info.size>4096||(info.mode&63))process.exit(2);
const {port,token}=JSON.parse(fs.readFileSync(file,'utf8'));
if(!Number.isInteger(port)||port<1024||port>65535||typeof token!=='string')process.exit(2);
const socket=net.connect({host:'127.0.0.1',port},()=>socket.write(JSON.stringify({token})+'\n'));
let buffer='';
const timer=setTimeout(()=>process.exit(3),85000);
socket.on('error',()=>process.exit(3));
socket.on('data',chunk=>{
  buffer+=chunk.toString();if(buffer.length>262144)process.exit(4);
  for(let end;(end=buffer.indexOf('\n'))>=0;){
    const line=buffer.slice(0,end);buffer=buffer.slice(end+1);
    const reply=JSON.parse(line);
    if(reply.authenticated)socket.write(JSON.stringify({id:1,...JSON.parse(request)})+'\n');
    else if(reply.id===1){
      if(!reply.ok)process.exit(5);
      process.stdout.write(JSON.stringify(reply.value));
      clearTimeout(timer);socket.end();
    }
  }
});
"""


class ManagedConsoleCodex(ConsoleCodex):
    """One immutable assignment, one managed agent and one isolated container."""

    def __init__(self, config, switch, identity):
        self.config, self.switch, self.identity = dict(config), switch, identity
        if not re.fullmatch(r"[a-f0-9]{32}", identity):
            raise ValueError("A managed assignment identity is required")
        for key in ("agent_id", "controller_id", "room_id"):
            if str(UUID(config[key])) != config[key]:
                raise ValueError("Managed identities must be canonical UUIDs")
        for key in ("container_name", "network_name"):
            if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,100}", config[key]):
                raise ValueError("Invalid managed container identity")
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", config["image_id"]):
            raise ValueError("A content-addressed managed image is required")
        if type(config["revision"]) is not int or config["revision"] < 1:
            raise ValueError("A pinned managed definition revision is required")
        self.binary = Path(config["docker_binary"]).resolve(strict=True)
        self.socket = Path(config["daemon_socket"]).resolve(strict=True)
        self.state = Path(config["state_root"]).resolve(strict=True)
        self.home = Path(config["home_root"]).resolve(strict=True)
        self.codex_name = config["agent_name"]
        self.agent_id = config["agent_id"]
        self.binding = {
            "backend": "console-controller-v2",
            "controller_id": config["controller_id"],
            "agent_id": self.agent_id,
            "definition_revision": config["revision"],
            "image_id": config["image_id"],
            "network_name": config["network_name"],
            "assignment_id": identity,
        }

    async def _docker(self, *args):
        proc = await asyncio.create_subprocess_exec(
            str(self.binary),
            "--host=unix://" + str(self.socket),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={"PATH": os.defpath, "HOME": "/nonexistent", "LANG": "C.UTF-8"},
        )
        try:
            output, _ = await asyncio.wait_for(proc.communicate(), 90)
        except (TimeoutError, asyncio.CancelledError):
            proc.kill()
            await proc.wait()
            raise
        if proc.returncode or len(output) > 1024 * 1024:
            raise OSError("Managed container control unavailable")
        return json.loads(output)

    async def assignment(self):
        credential = self.state / "secrets/controller-credential"
        info = credential.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 4096:
            raise ValueError("Controller credential must be a private regular file")
        prefix = "/v1/management/controllers/" + self.config["controller_id"]
        async with httpx.AsyncClient(
            base_url=self.switch.base_url,
            headers={"Switch-Controller-Protocol": "2"},
            timeout=20,
            follow_redirects=False,
        ) as client:
            issued = await client.post(
                prefix + "/token", json={"credential": credential.read_text().strip()}
            )
            issued.raise_for_status()
            response = await client.get(
                prefix + "/assignment",
                headers={"Authorization": "Bearer " + issued.json()["access_token"]},
            )
            response.raise_for_status()
            return response.json()

    async def validate(self, checkout):
        assigned = (await self.assignment())["agents"]
        if len(assigned) != 1:
            raise ValueError("The controller must be dedicated to one managed agent")
        agent = assigned[0]
        definition = agent["definition"]
        if (
            agent["agent_id"] != self.agent_id
            or agent["revision"] != self.config["revision"]
            or agent["desired_state"] != "running"
            or definition.get("provider") != "codex"
            or definition.get("name") != self.codex_name
            or definition.get("directory") != str(checkout)
            or definition.get("isolation") != "isolated"
            or definition.get("auto_approve") is not False
            or definition.get("instructions", "").partition("\n")[0]
            != "switchcrew-assignment:" + self.identity
        ):
            raise ValueError("Managed provider definition does not match this assignment")
        inspected = await self._docker("inspect", self.config["container_name"])
        if len(inspected) != 1:
            raise ValueError("Managed container identity is ambiguous")
        container = inspected[0]
        host, configured = container["HostConfig"], container["Config"]
        networks = container["NetworkSettings"]["Networks"]
        if (
            not container["State"]["Running"]
            or container["Image"] != self.config["image_id"]
            or configured.get("User", "").split(":")[0] in {"", "0", "root"}
            or host.get("Privileged")
            or not host.get("ReadonlyRootfs")
            or "ALL" not in host.get("CapDrop", [])
            or not any(s.startswith("no-new-privileges") for s in host.get("SecurityOpt", []))
            or host.get("PortBindings")
            or host.get("IpcMode") not in {"private", "none"}
            or not 0 < host.get("Memory", 0) <= 4096 * 1024**2
            or not 0 < host.get("NanoCpus", 0) <= 4_000_000_000
            or not 0 < host.get("PidsLimit", 0) <= 512
            or set(networks) != {self.config["network_name"]}
            or host.get("Dns") != ["127.0.0.1"]
        ):
            raise ValueError("Managed container isolation is not the pinned policy")
        network = await self._docker("network", "inspect", self.config["network_name"])
        if len(network) != 1 or network[0].get("Internal") is not True:
            raise ValueError("Managed execution needs an internal network")
        expected = {
            str(checkout): str(checkout),
            "/state": str(self.state),
            "/home/agent": str(self.home),
        }
        mounts = container["Mounts"]
        if len(mounts) != len(expected) or any(
            mount.get("Type") != "bind"
            or expected.get(mount.get("Destination")) != mount.get("Source")
            or not mount.get("RW")
            for mount in mounts
        ):
            raise ValueError("Managed container mounts must match the exact assignment")
        env = dict(part.split("=", 1) for part in configured.get("Env", []) if "=" in part)
        if (
            env.get("HTTPS_PROXY") != "http://flip-codex-egress:3128"
            or env.get("HTTP_PROXY") != "http://flip-codex-egress:3128"
            or any(
                key.startswith(("SWITCHCREW_", "GITHUB_", "GH_", "DOCKER_"))
                or key in {"NODE_OPTIONS", "NODE_PATH"}
                for key in env
            )
        ):
            raise ValueError("Managed environment contains unexpected credentials or overrides")
        health = await self._control({"health": True})
        if health.get("state") != "connected" or any(
            room != self.config["room_id"] for room in health.get("placements", {}).values()
        ):
            raise ValueError("Managed watcher is disconnected or assigned to another room")

    async def _control(self, request):
        return await self._docker(
            "exec",
            "-i",
            self.config["container_name"],
            "node",
            "-e",
            _CONTROL,
            "/state/watchers/" + self.agent_id + "/control.json",
            json.dumps(request),
        )

    async def find(self, identity):
        if identity != self.identity:
            raise ValueError("Managed session assignment changed")
        sessions = await self._control({"list": True})
        if not isinstance(sessions, list) or len(sessions) > 1:
            raise ValueError("Managed agent has sessions from other assignments")
        if not sessions:
            return None
        session = sessions[0]
        if session.get("agentId") != self.agent_id or any(
            room != self.config["room_id"] for room in session.get("roomIds", [])
        ):
            raise ValueError("Managed session room or agent differs")
        if not isinstance(session.get("sessionId"), str) or not session["sessionId"]:
            raise ValueError("Managed session has no real session identity")
        return session["sessionId"]

    async def create(self, identity):
        if identity != self.identity:
            raise ValueError("Managed session assignment changed")
        session = await self._control({"warm": {"roomId": self.config["room_id"]}})
        if not isinstance(session, str) or not session:
            raise ValueError("Console did not create a managed room session")
        return session
