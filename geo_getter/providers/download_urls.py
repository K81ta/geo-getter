from __future__ import annotations

import posixpath
import urllib.parse

from ..path_safety import safe_file_name


def normalize_download_url(raw_url: str) -> str:
    url = raw_url.strip()
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if url.startswith("ftp://ftp.sra.ebi.ac.uk/"):
        return "https://ftp.sra.ebi.ac.uk/" + url.removeprefix("ftp://ftp.sra.ebi.ac.uk/")
    if url.startswith("ftp.sra.ebi.ac.uk/"):
        return "https://" + url
    if url.startswith("fasp.sra.ebi.ac.uk/"):
        return ""
    return url


def filename_from_url(url: str, *, default: str, sanitize: bool = False) -> str:
    parsed = urllib.parse.urlparse(url)
    name = urllib.parse.unquote(posixpath.basename(parsed.path) or default)
    if sanitize:
        return safe_file_name(name, default)
    return name
