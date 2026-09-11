from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from switchcheck.aruba import parse_aruba_configuration
from switchcheck.comparison import compare_interfaces
from switchcheck.models import (
    CompareRequest,
    ComparisonResult,
    ImportResource,
    NetBoxBatchImportRequest,
    NetBoxBatchImportResult,
    NetBoxImportAction,
    NetBoxImportRequest,
    NetBoxImportResult,
)
from switchcheck.netbox import NetBoxClient, NetBoxError

PACKAGE_DIR = Path(__file__).parent

app = FastAPI(
    title="SwitchCheck",
    description="Compare an Aruba switch configuration with NetBox.",
    version="0.1.0",
)
app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/compare", response_model=ComparisonResult)
async def compare(payload: CompareRequest) -> ComparisonResult:
    aruba = parse_aruba_configuration(payload.config)
    if not aruba.interfaces:
        raise HTTPException(
            status_code=422,
            detail="No supported interface configuration was found in the Aruba config.",
        )

    try:
        async with NetBoxClient(
            str(payload.netbox_url), payload.token, verify_tls=payload.verify_tls
        ) as client:
            netbox = await client.get_configuration(
                payload.device, {vlan.vid for vlan in aruba.vlans}
            )
    except NetBoxError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return compare_interfaces(
        aruba.interfaces,
        netbox.interfaces,
        aruba.vlans,
        netbox.vlans,
        payload.config,
        netbox.rendered_config,
        netbox.rendered_config_error,
    )


@app.post("/api/netbox/import")
async def import_to_netbox(payload: NetBoxImportRequest) -> dict[str, str]:
    try:
        async with NetBoxClient(
            str(payload.netbox_url), payload.token, verify_tls=payload.verify_tls
        ) as client:
            message = await _execute_import(client, payload.device, payload)
    except NetBoxError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"message": message}


@app.post("/api/netbox/import-batch", response_model=NetBoxBatchImportResult)
async def import_batch_to_netbox(payload: NetBoxBatchImportRequest) -> NetBoxBatchImportResult:
    results: list[NetBoxImportResult] = []
    actions = sorted(payload.actions, key=_import_priority)
    async with NetBoxClient(
        str(payload.netbox_url), payload.token, verify_tls=payload.verify_tls
    ) as client:
        for action in actions:
            try:
                message = await _execute_import(client, payload.device, action)
                results.append(NetBoxImportResult(success=True, message=message))
            except NetBoxError as exc:
                results.append(NetBoxImportResult(success=False, message=str(exc)))

    applied = sum(result.success for result in results)
    return NetBoxBatchImportResult(
        applied=applied,
        failed=len(results) - applied,
        results=results,
    )


async def _execute_import(client: NetBoxClient, device: str, action: NetBoxImportAction) -> str:
    if action.resource is ImportResource.INTERFACE:
        if action.interface is None:
            raise NetBoxError("Interface data is required.")
        return await client.import_interface(
            device,
            action.interface,
            action.fields,
            create=action.create,
        )
    if action.vlan is None:
        raise NetBoxError("VLAN data is required.")
    return await client.import_vlan(
        device,
        action.vlan,
        action.fields,
        create=action.create,
    )


def _import_priority(action: NetBoxImportAction) -> int:
    if action.create and action.resource is ImportResource.VLAN:
        return 0
    if action.create and action.interface is not None:
        name = action.interface.name.replace(" ", "").lower()
        if name.startswith(("lag", "trk")):
            return 1
        return 2
    if action.resource is ImportResource.VLAN:
        return 3
    return 4
