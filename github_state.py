"""GitHub Contents API state, optimistic CAS before every attempted delivery.

Only non-secret dedup metadata is persisted to the public monitor-state branch.
The Actions GITHUB_TOKEN is ephemeral; no separate persistent OAuth grant is needed.
"""
from __future__ import annotations

import base64
import contextlib
import json
import os
import re
import urllib.request

from monitor import MAX_STATE_BYTES, NoRedirect, StateError, StateStore, decode_json, object_at, validate_state

REPOSITORY = "YuShenLiu06/codex-reset-monitor"
BRANCH = "monitor-state"


class GitHubStateStore(StateStore):
    def __init__(self, repository=None):
        # Intentionally do not create local state or initialize a missing branch.
        self.sha = None
        self.token = os.environ.get("GH_TOKEN", "")
        if not self.token:
            raise StateError("GitHub state requires GH_TOKEN")
        self.repository = repository or os.environ.get("GITHUB_REPOSITORY", REPOSITORY)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}", self.repository):
            raise StateError("Invalid GitHub state repository")
        self.url = f"https://api.github.com/repos/{self.repository}/contents/state.json"

    @contextlib.contextmanager
    def locked(self):
        # Workflow concurrency serializes normal invocations. API SHA CAS provides
        # the actual reservation guard against races or other runners.
        yield

    def _request(self, method, url, payload=None):
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url, data=body, method=method, headers={
            "Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
            "Content-Type": "application/json", "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "CodexResetMonitor/2.0"})
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            with opener.open(request, timeout=15) as response:
                if response.status not in (200, 201):
                    raise StateError("Unexpected GitHub state status")
                return object_at(decode_json(response.read(2 * MAX_STATE_BYTES + 1),
                                             2 * MAX_STATE_BYTES), "GitHub state response")
        except Exception:
            # An accepted write with a lost response may leave pending remotely.
            # Never proceed to delivery, retry the write, or reset missing state.
            raise StateError("Remote state unavailable or write unconfirmed; refusing delivery") from None

    def read(self):
        response = self._request("GET", self.url + f"?ref={BRANCH}")
        try:
            if response.get("encoding") != "base64" or type(response.get("size")) is not int:
                raise ValueError()
            if not 0 < response["size"] <= MAX_STATE_BYTES:
                raise ValueError()
            sha = response["sha"]
            if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
                raise ValueError()
            raw = base64.b64decode(response["content"].replace("\n", ""), validate=True)
            if len(raw) != response["size"]:
                raise ValueError()
            state = validate_state(decode_json(raw, MAX_STATE_BYTES))
            self.sha = sha
            return state
        except Exception:
            raise StateError("Remote state invalid; refusing to reset") from None

    def write(self, state):
        if self.sha is None:
            raise StateError("Remote state must be read before reserving delivery")
        raw = json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2,
                         allow_nan=False).encode("utf-8")
        if len(raw) > MAX_STATE_BYTES:
            raise StateError("Remote state exceeds limit; manual archival required")
        response = self._request("PUT", self.url, {
            "message": "Update monitor delivery ledger", "branch": BRANCH,
            "sha": self.sha, "content": base64.b64encode(raw).decode("ascii")})
        content = response.get("content")
        sha = content.get("sha") if isinstance(content, dict) else None
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise StateError("Remote write acknowledgement invalid; refusing delivery")
        self.sha = sha
