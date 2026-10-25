"""ErrorResponse / NoticeResponse fields built from pgAdmin's error text."""
import re


class ProtocolError(Exception):
    """The client sent something the proxy cannot handle; reported with SQLSTATE 08P01."""


def fields(msg, code=None, severity="ERROR"):
    """pgAdmin error text ("ERROR:  …\\nSQL state: 42P01\\nCharacter: 15") -> ErrorResponse fields."""
    return {
        "S": severity,
        "V": severity,
        "C": code or _field(msg, r"^SQL state: (\w{5})") or "XX000",
        "M": _field(msg, rf"^{severity}:\s+(.+)") or msg.strip(),
        "D": _field(msg, r"^Detail: (.+)"),
        "H": _field(msg, r"^Hint: (.+)"),
        "P": _field(msg, r"^Character: (\d+)"),
    }


def _field(text, pattern):
    m = re.search(pattern, text, re.M)
    return m and m.group(1).strip()
