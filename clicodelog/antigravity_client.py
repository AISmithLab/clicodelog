"""Read Antigravity's local Connect/JSON history API. Never launches a model.

The endpoint and CSRF token belong to one language-server process. Discovery
keeps them paired; credentials are neither logged nor saved in the backup.
Only loopback HTTP is accepted, with proxies and redirects disabled.
"""

import json
import os
import subprocess
import sys
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .editors import config_root
from .antigravity_discovery import endpoints
from .logging_setup import get_logger

SERVICE = "/exa.language_server_pb.LanguageServerService/"
log = get_logger(__name__)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _run(args):
    # Cold Windows PowerShell startup and CIM initialization can exceed five
    # seconds on shared runners and slower machines. Keep Unix probes short.
    program = args[0].lower().removesuffix(".exe")
    timeout = 30 if program in ("powershell", "pwsh") else 5
    try:
        return subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, check=False).stdout
    except subprocess.TimeoutExpired:
        log.warning("Antigravity discovery timed out after %s seconds (%s)", timeout, program)
        return ""
    except OSError:
        return ""


def _local_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
            or parsed.username or parsed.password or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment or not parsed.port):
        raise ValueError("Antigravity endpoint must be a loopback HTTP URL with a port")
    return url.rstrip("/")


class Client:
    def __init__(self, url, token=""):
        self.url = _local_url(url)
        self.token = token
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def call(self, method, payload=None):
        if method not in ("GetAllCascadeTrajectories", "GetCascadeTrajectory", "LoadTrajectory",
                          "GetCascadeTrajectorySteps"):
            raise ValueError("Not a history operation")
        req = Request(self.url + SERVICE + method, data=json.dumps(payload or {}).encode("utf-8"),
                      headers={"Content-Type": "application/json", "Connect-Protocol-Version": "1",
                               "x-codeium-csrf-token": self.token}, method="POST")
        with self.opener.open(req, timeout=2 if method == "GetAllCascadeTrajectories" else 10) as response:
            result = json.load(response)
        if not isinstance(result, dict):
            raise ValueError("Invalid history response")
        return result

    def fetch(self, sid):
        request = {"cascadeId": sid, "trajectoryVerbosity": "CLIENT_TRAJECTORY_VERBOSITY_FULL"}
        # Loading decrypts an existing file into the server's memory; it does
        # not send prompts, execute tools, or start a new conversation.
        try:
            response = self.call("GetCascadeTrajectory", request)
            if not isinstance(response.get("trajectory"), dict) or not response["trajectory"].get("steps"):
                raise ValueError("Trajectory not loaded")
        except (OSError, ValueError):
            self.call("LoadTrajectory", {"cascadeId": sid})
            response = self.call("GetCascadeTrajectory", request)
        trajectory = response.get("trajectory")
        if not isinstance(trajectory, dict) or not isinstance(trajectory.get("steps"), list):
            raise ValueError("History response contains no trajectory steps")
        total = response.get("numTotalSteps", len(trajectory["steps"]))
        if isinstance(total, int) and total > len(trajectory["steps"]):
            # Newer daemons page long trajectories. Do not save a partial read
            # as if it were complete, or replace a good previous snapshot.
            steps = []
            while len(steps) < total:
                page = self.call("GetCascadeTrajectorySteps", dict(request, stepOffset=len(steps)))
                batch = page.get("steps")
                if not isinstance(batch, list) or not batch or batch == steps[-len(batch):]:
                    raise ValueError("Incomplete trajectory history")
                steps.extend(batch)
            trajectory["steps"] = steps[:total]
        return trajectory


def discover() -> list[Client]:
    explicit = os.environ.get("CLICODELOG_ANTIGRAVITY_URL")
    if explicit:
        return [Client(explicit, os.environ.get("ANTIGRAVITY_CSRF_TOKEN", ""))]
    return [Client(url, token) for url, token in endpoints(sys.platform, _run, config_root())]
