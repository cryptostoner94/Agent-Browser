from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

import httpx

CORE_DIR = Path(__file__).resolve().parents[1] / "core-agent"
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from filesystem_tools import MacFilesystemMesh


@dataclass
class BrowserTaskRequest:
    url: str
    goal: str
    data_extraction_goal: str = ""
    max_steps: int = 25
    proxy_server: str = ""


@dataclass
class BrowserTaskResult:
    configured: bool
    task_id: str
    status: str
    url: str
    goal: str
    result: Dict[str, Any]
    saved_export: Dict[str, Any]


class SkyvernClient:
    """REST client for authorized Skyvern cloud-browser tasks.

    This client does not bypass CAPTCHAs, bot controls, paywalls, login gates, or rate limits.
    It only starts tasks for domains explicitly listed in SKYVERN_ALLOWED_DOMAINS.
    """

    TERMINAL_STATES = {"completed", "failed", "terminated", "canceled", "cancelled"}

    def __init__(self, mesh: Optional[MacFilesystemMesh] = None) -> None:
        self.api_key = os.getenv("SKYVERN_API_KEY", "").strip()
        self.base_url = os.getenv("SKYVERN_BASE_URL", "https://api.skyvern.com/v1").strip().rstrip("/")
        self.organization_id = os.getenv("SKYVERN_ORGANIZATION_ID", "").strip()
        self.allowed_domains = self._load_allowed_domains()
        self.timeout_seconds = int(os.getenv("SKYVERN_DEFAULT_TIMEOUT_SECONDS", "600"))
        self.proxy_server = os.getenv("PROXY_SERVER", "").strip()
        self.compliance_mode = os.getenv("NEXUS_COMPLIANCE_MODE", "true").lower() != "false"
        self.mesh = mesh or MacFilesystemMesh()

    def health(self) -> Dict[str, Any]:
        return {
            "configured": bool(self.api_key),
            "base_url": self.base_url,
            "allowed_domains": sorted(self.allowed_domains),
            "compliance_mode": self.compliance_mode,
        }

    def dispatch(self, request: BrowserTaskRequest, wait: bool = True) -> BrowserTaskResult:
        self._validate_request(request)
        if not self.api_key:
            payload = {
                "mode": "not_configured",
                "message": "SKYVERN_API_KEY is missing. Configure .env to enable live cloud browser tasks.",
                "request": asdict(request),
            }
            saved = self.mesh.write_json_export(f"skyvern-dry-run-{int(time.time())}.json", payload)
            return BrowserTaskResult(False, "", "not_configured", request.url, request.goal, payload, saved)

        created = self.create_task(request)
        task_id = str(created.get("task_id") or created.get("id") or "")
        if not task_id:
            raise RuntimeError(f"Skyvern did not return a task id: {created}")

        final_payload = self.wait_task(task_id) if wait else created
        normalized = self.normalize_task_result(final_payload)
        saved = self.mesh.write_json_export(f"skyvern-task-{task_id}.json", normalized)
        return BrowserTaskResult(True, task_id, normalized.get("status", "unknown"), request.url, request.goal, normalized, saved)

    def create_task(self, request: BrowserTaskRequest) -> Dict[str, Any]:
        headers = self._headers()
        payload: Dict[str, Any] = {
            "url": request.url,
            "navigation_goal": request.goal,
            "data_extraction_goal": request.data_extraction_goal or request.goal,
            "max_steps": max(1, min(request.max_steps, 50)),
        }
        proxy = request.proxy_server or self.proxy_server
        if proxy:
            payload["proxy_location"] = proxy

        with httpx.Client(timeout=60.0) as client:
            response = client.post(f"{self.base_url}/tasks", headers=headers, json=payload)
            response.raise_for_status()
            return response.json()

    def get_task(self, task_id: str) -> Dict[str, Any]:
        if not task_id.strip():
            raise ValueError("task_id is required.")
        with httpx.Client(timeout=60.0) as client:
            response = client.get(f"{self.base_url}/tasks/{task_id}", headers=self._headers())
            response.raise_for_status()
            return response.json()

    def wait_task(self, task_id: str) -> Dict[str, Any]:
        deadline = time.time() + self.timeout_seconds
        last_payload: Dict[str, Any] = {}
        while time.time() < deadline:
            last_payload = self.get_task(task_id)
            status = str(last_payload.get("status", "")).lower()
            if status in self.TERMINAL_STATES:
                return last_payload
            time.sleep(4.0)
        raise TimeoutError(f"Skyvern task {task_id} did not finish within {self.timeout_seconds} seconds.")

    def normalize_task_result(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        extracted = self.extract_network_payloads(payload)
        return {
            "status": payload.get("status", "unknown"),
            "task_id": payload.get("task_id") or payload.get("id", ""),
            "created_at": payload.get("created_at", ""),
            "modified_at": payload.get("modified_at", ""),
            "failure_reason": payload.get("failure_reason", ""),
            "extracted_information": payload.get("extracted_information") or payload.get("extracted_data") or {},
            "screenshots": payload.get("screenshots", []),
            "recording_url": payload.get("recording_url", ""),
            "network_payloads": extracted,
            "raw": payload,
        }

    def extract_network_payloads(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract JSON-like artifacts returned by the browser provider.

        The method reads provider-returned task artifacts only. It does not intercept private user traffic.
        """
        candidates: List[Any] = []
        for key in ("network_logs", "xhr", "har", "artifacts", "browser_logs"):
            value = payload.get(key)
            if value:
                candidates.append(value)

        extracted: List[Dict[str, Any]] = []
        for candidate in candidates:
            if isinstance(candidate, list):
                for item in candidate:
                    parsed = self._parse_network_item(item)
                    if parsed:
                        extracted.append(parsed)
            elif isinstance(candidate, dict):
                parsed = self._parse_network_item(candidate)
                if parsed:
                    extracted.append(parsed)
        return extracted[:200]

    def parse_job_like_records(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Normalize authorized job-style extracted records from provider task output."""
        records: List[Dict[str, Any]] = []
        extracted = payload.get("extracted_information") or payload.get("extracted_data") or {}
        stack: List[Any] = [extracted]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                keys = {k.lower() for k in item.keys()}
                if {"title", "company"}.intersection(keys) or {"job_title", "employer"}.intersection(keys):
                    records.append(item)
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
        return records[:500]

    def _headers(self) -> Dict[str, str]:
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        if self.organization_id:
            headers["x-skyvern-organization-id"] = self.organization_id
        return headers

    def _load_allowed_domains(self) -> set[str]:
        raw = os.getenv("SKYVERN_ALLOWED_DOMAINS", "example.com,hackernews.org,news.ycombinator.com")
        return {domain.strip().lower() for domain in raw.split(",") if domain.strip()}

    def _validate_request(self, request: BrowserTaskRequest) -> None:
        if not self.compliance_mode:
            raise ValueError("Compliance mode must remain enabled for browser task dispatch.")
        parsed = urlparse(request.url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("A valid http(s) URL is required.")
        hostname = (parsed.hostname or "").lower()
        if not self._domain_allowed(hostname):
            raise ValueError(
                f"Domain '{hostname}' is not allowed. Add it to SKYVERN_ALLOWED_DOMAINS only when you are authorized to automate it."
            )
        forbidden_goal_terms = {
            "bypass captcha",
            "avoid detection",
            "evade bot",
            "stealth",
            "scrape behind login",
            "credential theft",
            "rate limit bypass",
        }
        lowered_goal = f"{request.goal} {request.data_extraction_goal}".lower()
        if any(term in lowered_goal for term in forbidden_goal_terms):
            raise ValueError("The browser task asks for disallowed evasion or unauthorized access behavior.")

    def _domain_allowed(self, hostname: str) -> bool:
        for domain in self.allowed_domains:
            if hostname == domain or hostname.endswith("." + domain):
                return True
        return False

    def _parse_network_item(self, item: Any) -> Optional[Dict[str, Any]]:
        if isinstance(item, dict):
            url = str(item.get("url") or item.get("request_url") or "")
            method = str(item.get("method") or item.get("request_method") or "")
            status = item.get("status") or item.get("status_code")
            body = item.get("body") or item.get("response_body") or item.get("json")
            parsed_body = body
            if isinstance(body, str):
                try:
                    parsed_body = json.loads(body)
                except json.JSONDecodeError:
                    parsed_body = body[:2000]
            return {"url": url, "method": method, "status": status, "body": parsed_body}
        if isinstance(item, str):
            try:
                loaded = json.loads(item)
                if isinstance(loaded, dict):
                    return self._parse_network_item(loaded)
            except json.JSONDecodeError:
                return None
        return None


if __name__ == "__main__":
    client = SkyvernClient()
    print(json.dumps(client.health(), indent=2))
