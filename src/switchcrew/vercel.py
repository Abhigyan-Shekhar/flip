"""Generate a static site and publish it to a dedicated Vercel preview project."""

import asyncio
import base64
import json
import re
from html import escape
from importlib.resources import files
from string import Template

import httpx

_API = "https://api.vercel.com/v13/deployments"
_DEPLOYMENT_ID = re.compile(r"dpl_[A-Za-z0-9]{1,100}")
_PREVIEW_HOST = re.compile(r"[a-z0-9-]{1,63}\.vercel\.app")
_STATIC_POLICY = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'; sandbox"
)


class PreviewError(Exception):
    """A safe status message for the Discord requester."""


def render_site(title: str, description: str) -> str:
    template = files("switchcrew").joinpath("templates/launch_site.html").read_text("utf-8")
    return Template(template).substitute(title=escape(title), description=escape(description))


async def deploy_preview(
    client: httpx.AsyncClient,
    *,
    token: str,
    project: str,
    team_id: str,
    title: str,
    description: str,
) -> str:
    return await deploy_html(
        client,
        token=token,
        project=project,
        team_id=team_id,
        page=render_site(title, description),
    )


async def deploy_html(client, *, token, project, team_id, page, generated=False):
    deployment_files = {"index.html": page}
    if generated:
        deployment_files["vercel.json"] = json.dumps(
            {
                "headers": [
                    {
                        "source": "/(.*)",
                        "headers": [{"key": "Content-Security-Policy", "value": _STATIC_POLICY}],
                    }
                ],
            }
        )
    headers = {"Authorization": f"Bearer {token}"}
    params = {"teamId": team_id} if team_id else {}
    deployment_id = None
    try:
        async with asyncio.timeout(90):
            response = await client.post(
                _API,
                headers=headers,
                params=params,
                json={
                    "name": project,
                    "projectSettings": {"framework": None},
                    "files": [
                        {
                            "file": name,
                            "data": base64.b64encode(content.encode()).decode("ascii"),
                            "encoding": "base64",
                        }
                        for name, content in deployment_files.items()
                    ],
                },
            )
            response.raise_for_status()
            deployment = response.json()
            candidate = deployment.get("id") if isinstance(deployment, dict) else None
            if not isinstance(candidate, str) or not _DEPLOYMENT_ID.fullmatch(candidate):
                raise PreviewError(
                    "Vercel did not return a valid deployment ID. Check its dashboard."
                )
            deployment_id = candidate
            while True:
                state = deployment.get("readyState")
                if state == "READY":
                    host = deployment.get("url")
                    if isinstance(host, str) and _PREVIEW_HOST.fullmatch(host):
                        return f"https://{host}"
                    raise PreviewError(
                        f"Vercel reported {deployment_id} ready without a preview URL."
                    )
                if state in ("ERROR", "CANCELED"):
                    raise PreviewError(f"Vercel marked deployment {deployment_id} {state.lower()}.")
                if state not in ("QUEUED", "INITIALIZING", "BUILDING", "DEPLOYING"):
                    raise PreviewError(f"Vercel returned an unknown state for {deployment_id}.")
                await asyncio.sleep(2)
                response = await client.get(
                    f"{_API}/{deployment_id}", headers=headers, params=params
                )
                response.raise_for_status()
                deployment = response.json()
                if not isinstance(deployment, dict) or deployment.get("id") != deployment_id:
                    raise PreviewError(f"Vercel returned an unexpected status for {deployment_id}.")
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        detail = f" for {deployment_id}" if deployment_id else ""
        raise PreviewError(f"Vercel returned HTTP {status}{detail}. Check its dashboard.") from None
    except (TimeoutError, httpx.RequestError, ValueError):
        detail = f" for {deployment_id}" if deployment_id else ""
        raise PreviewError(
            f"Could not confirm the Vercel preview{detail}. Check its dashboard."
        ) from None
