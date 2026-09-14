import pytest

from switchcheck.models import SshConfigRequest
from switchcheck.ssh import SshConfigError, fetch_configuration


@pytest.mark.asyncio
async def test_ssh_requires_request_scoped_credentials() -> None:
    request = SshConfigRequest(host="switch.example", username="admin")

    with pytest.raises(SshConfigError, match="password or private key"):
        await fetch_configuration(request)
