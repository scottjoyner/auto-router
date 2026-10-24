"""Owner-bound token-file loader for scoped local model providers.

Reads credential bytes only into process memory. Never logs token contents,
prints them, exports them to environment, or follows token-file symlinks.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import stat

_VISIBLE_TOKEN = re.compile(r"^[!-~]{12,2048}$")


class ProviderCredentialHold(Exception):
    """Configured provider token is missing or unsafe; no anonymous fallback."""


def read_provider_token_file(value: str) -> str:
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ProviderCredentialHold("provider_token_absolute_file_required")
    fd = None
    try:
        fd = os.open(value, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise ProviderCredentialHold("provider_token_owner_or_type_invalid")
        if info.st_mode & 0o077 or not info.st_mode & stat.S_IRUSR:
            raise ProviderCredentialHold("provider_token_mode_invalid")
        if not 12 <= info.st_size <= 2050:
            raise ProviderCredentialHold("provider_token_length_invalid")
        raw = os.read(fd, 2051)
        if len(raw) != info.st_size:
            raise ProviderCredentialHold("provider_token_read_race")
        try:
            token = raw.decode("ascii").removesuffix("\n")
        except UnicodeError:
            raise ProviderCredentialHold("provider_token_encoding_invalid") from None
        if not _VISIBLE_TOKEN.fullmatch(token):
            raise ProviderCredentialHold("provider_token_invalid_format")
        return token
    except OSError:
        raise ProviderCredentialHold("provider_token_unavailable") from None
    finally:
        if fd is not None:
            os.close(fd)
