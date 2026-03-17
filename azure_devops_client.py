import base64
from typing import Any

import requests


DEFAULT_FIELDS = [
    "System.Id",
    "System.Title",
    "System.Description",
    "System.State",
    "System.WorkItemType",
    "System.AssignedTo",
    "System.CreatedDate",
    "System.ChangedDate",
    "System.Parent",
    "Microsoft.VSTS.Scheduling.OriginalEstimate",
    "Microsoft.VSTS.Scheduling.RemainingWork",
    "Microsoft.VSTS.Scheduling.CompletedWork",
    "Microsoft.VSTS.Scheduling.StartDate",
    "Microsoft.VSTS.Scheduling.FinishDate",
]


class AzureDevOpsClient:
    def __init__(self, org_url: str, project: str, pat: str) -> None:
        self.org_url = org_url.rstrip("/")
        self.project = project
        token = base64.b64encode(f":{pat}".encode("utf-8")).decode("utf-8")
        self.headers = {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json-patch+json",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        api_version: str = "7.1",
        params: dict[str, Any] | None = None,
        json: Any = None,
        data: Any = None,
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        url = f"{self.org_url}/{self.project}/_apis/{path}"
        query = {"api-version": api_version}
        if params:
            query.update(params)

        headers = dict(self.headers)
        if extra_headers:
            headers.update(extra_headers)

        response = requests.request(
            method,
            url,
            headers=headers,
            params=query,
            json=json,
            data=data,
            timeout=30,
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            detail = response.text.strip()
            if detail:
                raise requests.HTTPError(
                    f"{exc}. Azure DevOps response: {detail}",
                    response=response,
                ) from exc
            raise
        if response.content:
            return response.json()
        return {}

    def query_my_work_items(self, user_email: str, work_item_type: str | None, state: str | None) -> list[int]:
        clauses = [f"[System.AssignedTo] = '{user_email}'"]
        if work_item_type and work_item_type != "Any":
            clauses.append(f"[System.WorkItemType] = '{work_item_type}'")
        if state and state != "Any":
            clauses.append(f"[System.State] = '{state}'")

        wiql = {
            "query": (
                "SELECT [System.Id] "
                "FROM WorkItems "
                f"WHERE {' AND '.join(clauses)} "
                "ORDER BY [System.ChangedDate] DESC"
            )
        }
        result = self._request("POST", "wit/wiql", json=wiql, extra_headers={"Content-Type": "application/json"})
        return [item["id"] for item in result.get("workItems", [])]

    def query_assigned_user_stories(self, user_email: str, state: str | None = None) -> list[int]:
        clauses = [f"[System.AssignedTo] = '{user_email}'", "[System.WorkItemType] = 'User Story'"]
        if state and state != "Any":
            clauses.append(f"[System.State] = '{state}'")

        wiql = {
            "query": (
                "SELECT [System.Id] "
                "FROM WorkItems "
                f"WHERE {' AND '.join(clauses)} "
                "ORDER BY [System.ChangedDate] DESC"
            )
        }
        result = self._request("POST", "wit/wiql", json=wiql, extra_headers={"Content-Type": "application/json"})
        return [item["id"] for item in result.get("workItems", [])]

    def query_child_tasks(self, parent_id: int) -> list[int]:
        wiql = {
            "query": (
                "SELECT [System.Id] "
                "FROM WorkItems "
                f"WHERE [System.Parent] = {parent_id} "
                "AND [System.WorkItemType] = 'Task' "
                "ORDER BY [System.ChangedDate] DESC"
            )
        }
        result = self._request("POST", "wit/wiql", json=wiql, extra_headers={"Content-Type": "application/json"})
        return [item["id"] for item in result.get("workItems", [])]

    def get_work_items(self, ids: list[int], fields: list[str] | None = None) -> list[dict[str, Any]]:
        if not ids:
            return []
        params = {
            "ids": ",".join(str(item_id) for item_id in ids),
            "fields": ",".join(fields or DEFAULT_FIELDS),
        }
        result = self._request("GET", "wit/workitems", params=params)
        return result.get("value", [])

    def update_work_item(
        self,
        work_item_id: int,
        *,
        state: str | None = None,
        assigned_to: str | None = None,
        title: str | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ops = []
        if title:
            ops.append({"op": "add", "path": "/fields/System.Title", "value": title})
        if state:
            ops.append({"op": "add", "path": "/fields/System.State", "value": state})
        if assigned_to:
            ops.append({"op": "add", "path": "/fields/System.AssignedTo", "value": assigned_to})
        if extra_fields:
            for field_name, field_value in extra_fields.items():
                if field_value is None or field_value == "":
                    continue
                ops.append({"op": "add", "path": f"/fields/{field_name}", "value": field_value})
        return self._request("PATCH", f"wit/workitems/{work_item_id}", json=ops)

    def create_task(
        self,
        title: str,
        description: str,
        assigned_to: str,
        parent_id: int | None = None,
    ) -> dict[str, Any]:
        ops = [
            {"op": "add", "path": "/fields/System.Title", "value": title},
            {"op": "add", "path": "/fields/System.Description", "value": description or ""},
            {"op": "add", "path": "/fields/System.AssignedTo", "value": assigned_to},
        ]
        if parent_id:
            ops.append(
                {
                    "op": "add",
                    "path": "/relations/-",
                    "value": {
                        "rel": "System.LinkTypes.Hierarchy-Reverse",
                        "url": f"{self.org_url}/_apis/wit/workItems/{parent_id}",
                    },
                }
            )
        return self._request("PATCH", "wit/workitems/$Task", json=ops)

    def add_comment(self, work_item_id: int, text: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"wit/workItems/{work_item_id}/comments",
            api_version="7.1-preview.4",
            json={"text": text},
            extra_headers={"Content-Type": "application/json"},
        )
