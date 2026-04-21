"""HTTP client for the code_graph_search REST API."""

from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)


class GraphClient:
    """Thin wrapper around code_graph_search REST endpoints."""

    def __init__(self, base_url: str = "http://localhost:8080", timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GraphClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- Health ----------------------------------------------------------------

    def healthy(self) -> bool:
        try:
            r = self._client.get("/api/health")
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    # -- Search ----------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        element_type: str | None = None,
        repo_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"q": query, "limit": limit}
        if element_type:
            params["type"] = element_type
        if repo_id:
            params["repo"] = repo_id
        r = self._client.get("/api/search", params=params)
        r.raise_for_status()
        return r.json()

    def search_by_name(self, name: str, **kwargs: Any) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"name": name, **kwargs}
        r = self._client.get("/api/search/by-name", params=params)
        r.raise_for_status()
        return r.json()

    # -- Elements --------------------------------------------------------------

    def get_element(self, element_id: str) -> dict[str, Any]:
        r = self._client.get(f"/api/elements/{element_id}")
        r.raise_for_status()
        return r.json()

    def get_snippet(self, element_id: str, context: int = 5) -> dict[str, Any]:
        r = self._client.get(
            f"/api/elements/{element_id}/snippet", params={"context": context}
        )
        r.raise_for_status()
        return r.json()

    def get_children(self, element_id: str) -> list[dict[str, Any]]:
        r = self._client.get(f"/api/elements/{element_id}/children")
        r.raise_for_status()
        return r.json()

    def get_parent(self, element_id: str) -> dict[str, Any] | None:
        r = self._client.get(f"/api/elements/{element_id}/parent")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    # -- Call graph -------------------------------------------------------------

    def get_callers(self, element_id: str) -> list[dict[str, Any]]:
        r = self._client.get(f"/api/elements/{element_id}/callers")
        r.raise_for_status()
        return r.json()

    def get_callees(self, element_id: str) -> list[dict[str, Any]]:
        r = self._client.get(f"/api/elements/{element_id}/callees")
        r.raise_for_status()
        return r.json()

    # -- Type hierarchy --------------------------------------------------------

    def get_hierarchy(self, element_id: str) -> dict[str, Any]:
        r = self._client.get(f"/api/elements/{element_id}/hierarchy")
        r.raise_for_status()
        return r.json()

    # -- Graph traversal -------------------------------------------------------

    def shortest_path(
        self, from_id: str, to_id: str
    ) -> dict[str, Any]:
        r = self._client.get(
            "/api/graph/shortest-path",
            params={"from": from_id, "to": to_id},
        )
        r.raise_for_status()
        return r.json()

    # -- Repository management -------------------------------------------------

    def list_repos(self) -> list[dict[str, Any]]:
        r = self._client.get("/api/repos")
        r.raise_for_status()
        return r.json()

    def add_repo(
        self,
        repo_id: str,
        name: str,
        path: str,
        languages: list[str] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"id": repo_id, "name": name, "path": path}
        if languages:
            payload["languages"] = languages
        r = self._client.post("/api/repos", json=payload)
        r.raise_for_status()
        return r.json()

    def reindex_repo(self, repo_id: str) -> dict[str, Any]:
        r = self._client.post(f"/api/repos/{repo_id}/reindex")
        r.raise_for_status()
        return r.json()

    # -- File-level queries ----------------------------------------------------

    def get_file_outline(self, repo_id: str, path: str) -> list[dict[str, Any]]:
        """Get all elements defined in a specific file."""
        results = self.search(f"file:{path}", repo_id=repo_id, limit=100)
        return [r for r in results if r.get("filePath", "").endswith(path)]
