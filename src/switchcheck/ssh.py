import asyncio

import asyncssh

from switchcheck.models import SshConfigRequest


class SshConfigError(RuntimeError):
    """A safe, user-facing switch connection error."""


async def fetch_configuration(request: SshConfigRequest) -> str:
    if not request.password and not request.private_key:
        raise SshConfigError("Provide an SSH password or private key.")

    client_keys = [request.private_key] if request.private_key else None
    known_hosts: str | None = request.known_hosts
    try:
        async with asyncio.timeout(30):
            async with asyncssh.connect(
                request.host,
                port=request.port,
                username=request.username,
                password=request.password,
                client_keys=client_keys,
                known_hosts=known_hosts,
            ) as connection:
                if request.sftp_path:
                    async with connection.start_sftp_client() as sftp:
                        content = await sftp.read(request.sftp_path)
                    return content.decode() if isinstance(content, bytes) else content
                result = await connection.run(request.command, check=True)
                return result.stdout
    except TimeoutError as exc:
        raise SshConfigError("The switch connection timed out.") from exc
    except (asyncssh.Error, OSError) as exc:
        raise SshConfigError(f"Could not retrieve the switch configuration: {exc}") from exc
