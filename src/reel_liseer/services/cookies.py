import base64
import logging
import os
import re
import tempfile
import threading
import time
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

from reel_liseer import config

logger = logging.getLogger(__name__)

HEADER = "# Netscape HTTP Cookie File\n"
_SLOT_RE = re.compile(r"[^a-z0-9_-]+")
_lock = threading.RLock()


class CookieError(ValueError):
    """Raised for an unusable upload; the message is safe to show to the admin."""
@dataclass(frozen=True)
class Cookie:
    domain: str
    include_subdomains: str
    path: str
    secure: str
    expires: int
    name: str
    value: str
    http_only: bool = False

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.domain.lstrip(".").lower(), self.path, self.name)

    def to_line(self) -> str:
        domain = ("#HttpOnly_" if self.http_only else "") + self.domain
        return "\t".join(
            (domain, self.include_subdomains, self.path, self.secure, str(self.expires), self.name, self.value)
        )


def cookies_dir() -> Path:
    path = Path(config.COOKIES_DIR)
    path.mkdir(parents=True, exist_ok=True)
    with suppress(OSError):
        path.chmod(0o700)
    return path


def slot_name(raw: str | None) -> str:
    name = _SLOT_RE.sub("-", (raw or "").strip().lower()).strip("-")[:40]
    return name or "universal"


def _slot_path(slot: str) -> Path:
    return cookies_dir() / f"{slot_name(slot)}.txt"


_HOST_RE = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+")


def normalize_domain(raw: str) -> str:
    host = re.sub(r"^[a-z]+://", "", raw.strip().lower()).split("/")[0].split(":")[0].lstrip(".")
    if host.startswith("www."):
        host = host[4:]
    if not _HOST_RE.fullmatch(host):
        raise CookieError(f"'{raw.strip()}' is not a valid domain (example: vimeo.com).")
    return host


def parse_domains(raw: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(normalize_domain(part) for part in re.split(r"[,\s]+", raw or "") if part))


def _domain_allowed(domain: str, allowed: tuple[str, ...] | None) -> bool:
    if allowed is None:
        return True
    host = domain.lstrip(".").lower()
    return any(host == d or host.endswith("." + d) for d in allowed)


def parse(text: str, extra_domains: tuple[str, ...] = (), *, filter_domains: bool = True) -> tuple[list[Cookie], dict]:
    stats = {"lines": 0, "kept": 0, "skipped_domain": 0, "expired": 0, "invalid": 0}
    kept: dict[tuple, Cookie] = {}
    now = time.time()
    allowed = (*config.COOKIE_DOMAINS, *extra_domains) if filter_domains else None

    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip("﻿ \t")
        if not line:
            continue
        http_only = False
        if line.startswith("#HttpOnly_"):
            http_only = True
            line = line[len("#HttpOnly_"):]
        elif line.startswith("#"):
            continue

        stats["lines"] += 1
        fields = line.split("\t")
        if len(fields) != 7:
            fields = line.split(None, 6)  # tolerate tabs turned into spaces by copy/paste
        if len(fields) != 7:
            stats["invalid"] += 1
            continue

        domain, subdomains, path, secure, expires, name, value = (f.strip() for f in fields)
        if (
            not domain
            or subdomains.upper() not in ("TRUE", "FALSE")
            or secure.upper() not in ("TRUE", "FALSE")
            or not path.startswith("/")
            or not name
            or not re.fullmatch(r"-?\d+", expires)
        ):
            stats["invalid"] += 1
            continue

        if not _domain_allowed(domain, allowed):
            stats["skipped_domain"] += 1
            continue

        expiry = max(int(expires), 0)
        if 0 < expiry < now:
            stats["expired"] += 1
            continue

        cookie = Cookie(domain, subdomains.upper(), path, secure.upper(), expiry, name, value, http_only)
        kept[cookie.key] = cookie

    stats["kept"] = len(kept)
    return list(kept.values()), stats


def _write_private(path: Path, content: str) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
    os.replace(tmp, path)


def save_upload(raw: bytes, slot: str | None = None, extra_domains: tuple[str, ...] = ()) -> dict:
    if not raw or not raw.strip():
        raise CookieError("The file is empty.")
    if len(raw) > config.COOKIES_MAX_BYTES:
        raise CookieError(f"The file is larger than {config.COOKIES_MAX_BYTES // 1024} KB.")
    if b"\x00" in raw:
        raise CookieError("This is not a text file. Export a Netscape-format cookies.txt.")

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CookieError("The file is not valid UTF-8 text.") from exc

    cookies, stats = parse(text, extra_domains)
    if not cookies:
        hint = ""
        if stats["skipped_domain"]:
            hint = f" {stats['skipped_domain']} cookie(s) were for sites that are not allowed; add the site under 'Other sites' or in COOKIE_DOMAINS."
        elif stats["lines"] == 0 or stats["invalid"] == stats["lines"]:
            hint = " The file does not look like Netscape cookies.txt (7 tab-separated columns per line)."
        raise CookieError("No usable cookies found." + hint)

    slot = slot_name(slot)
    body = HEADER + "\n".join(c.to_line() for c in sorted(cookies, key=lambda c: c.key)) + "\n"
    with _lock:
        _write_private(_slot_path(slot), body)
        _rebuild_merged()

    logger.info("Stored %s cookies in slot %r (%s skipped)", stats["kept"], slot, stats["skipped_domain"])
    return {"slot": slot, **stats}


def delete_slot(slot: str) -> bool:
    with _lock:
        path = _slot_path(slot)
        existed = path.is_file()
        path.unlink(missing_ok=True)
        _rebuild_merged()
    return existed


def _load_slot(path: Path) -> list[Cookie]:
    try:
        return parse(path.read_text(encoding="utf-8"), filter_domains=False)[0]
    except OSError:
        return []


def _merged_path() -> Path:
    return cookies_dir() / "_merged.cookies"


def _rebuild_merged() -> None:
    merged: dict[tuple, Cookie] = {}
    for path in sorted(cookies_dir().glob("*.txt")):
        for cookie in _load_slot(path):
            merged[cookie.key] = cookie
    target = _merged_path()
    if not merged:
        target.unlink(missing_ok=True)
        return
    _write_private(target, HEADER + "\n".join(c.to_line() for c in merged.values()) + "\n")


def seed_from_env() -> None:
    encoded = os.getenv("COOKIES_B64", "").strip()
    if not encoded:
        return
    with _lock:
        if any(cookies_dir().glob("*.txt")):
            return
        try:
            summary = save_upload(base64.b64decode(encoded), "env")
            logger.info("Seeded %s cookies from COOKIES_B64", summary["kept"])
        except (ValueError, CookieError) as exc:
            logger.warning("COOKIES_B64 ignored: %s", exc)


def status() -> list[dict]:
    result = []
    with _lock:
        for path in sorted(cookies_dir().glob("*.txt")):
            cookies = _load_slot(path)
            domains: dict[str, int] = {}
            soonest = 0
            for cookie in cookies:
                host = cookie.domain.lstrip(".").lower()
                domains[host] = domains.get(host, 0) + 1
                if cookie.expires and (not soonest or cookie.expires < soonest):
                    soonest = cookie.expires
            result.append({
                "slot": path.stem,
                "count": len(cookies),
                "domains": dict(sorted(domains.items())),
                "earliest_expiry": soonest or None,
                "updated": int(path.stat().st_mtime),
            })
    return result


@contextmanager
def cookiefile():
    with _lock:
        source = _merged_path()
        if not source.is_file() or source.stat().st_size == 0:
            yield None
            return
        fd, tmp_name = tempfile.mkstemp(prefix="ytdlp-cookies-", suffix=".txt")
        with os.fdopen(fd, "wb") as handle:
            handle.write(source.read_bytes())
    try:
        yield tmp_name
    finally:
        Path(tmp_name).unlink(missing_ok=True)
