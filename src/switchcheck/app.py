from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from switchcheck.aruba import parse_aruba_configuration
from switchcheck.comparison import compare_interfaces
from switchcheck.models import CompareRequest, ComparisonResult
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
