"""VISA / instrument error classification.

Collapses the many ways a connect / scan / read can fail into a small,
operator-actionable taxonomy so the GUI log can say *what kind* of
failure happened and *what to do next*, instead of dumping a raw
PyVISA exception string.

The taxonomy deliberately stays small:

* :attr:`VisaErrorKind.NO_VISA`    — system VISA library missing.
* :attr:`VisaErrorKind.NO_DEVICE`  — resource not found (wrong address,
  cable unplugged, instrument off).
* :attr:`VisaErrorKind.TIMEOUT`    — instrument did not reply in time.
* :attr:`VisaErrorKind.SCPI_ERROR` — instrument replied with an error.
* :attr:`VisaErrorKind.TRANSPORT`  — serial / ASRL / low-level OS
  failure that usually means "wrong COM port, wrong baud, or port
  already in use".
* :attr:`VisaErrorKind.UNKNOWN`    — fall-through; details preserved
  in the wrapped exception so the operator can still see what
  happened.

The module uses only stdlib + the already-required :mod:`pyvisa`
import.  It adds NO new runtime dependency.
"""
from __future__ import annotations

import enum
from typing import Optional


class VisaErrorKind(str, enum.Enum):
    """Small, operator-actionable VISA failure taxonomy."""

    NO_VISA    = "no_visa"
    NO_DEVICE  = "no_device"
    TIMEOUT    = "timeout"
    SCPI_ERROR = "scpi_error"
    TRANSPORT  = "transport"
    UNKNOWN    = "unknown"


#: One-line remediation hint per error kind.  Intentionally short so
#: it fits into a log line; the full INSTALL_prereqs.md expands on
#: each of these.
REMEDIATION: dict[VisaErrorKind, str] = {
    VisaErrorKind.NO_VISA:
        "Install Keysight IO Libraries Suite or NI-VISA on this PC "
        "(see docs/INSTALL_prereqs.md).",
    VisaErrorKind.NO_DEVICE:
        "Check the VISA address and that the instrument is powered, "
        "connected, and visible in Keysight Connection Expert / NI MAX.",
    VisaErrorKind.TIMEOUT:
        "Instrument did not reply in time. Verify address, cabling, "
        "power, or raise the driver timeout.",
    VisaErrorKind.SCPI_ERROR:
        "Instrument rejected a SCPI command. Check the front panel for "
        "an error and try again after *RST or a power cycle.",
    VisaErrorKind.TRANSPORT:
        "Serial / transport failure. Check COM port, baud rate, cable, "
        "and that no other program is using the port.",
    VisaErrorKind.UNKNOWN:
        "Unexpected instrument error — see details for triage.",
}


class ClassifiedVisaError(Exception):
    """Exception wrapping a VISA / instrument failure with a
    machine-inspectable :class:`VisaErrorKind` tag.

    The string form is the operator-facing one-line message, so code
    that only knows how to append a string to the GUI log
    (:func:`utils.append_log`) automatically gets a useful message.
    Callers that want structured triage read :attr:`kind` and
    :attr:`original`.
    """

    def __init__(self, kind: VisaErrorKind, original: BaseException, *,
                 context: str = ""):
        self.kind = kind
        self.original = original
        self.context = context
        super().__init__(format_for_operator(self))

    def remediation(self) -> str:
        return REMEDIATION.get(self.kind, REMEDIATION[VisaErrorKind.UNKNOWN])


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------
def classify(exc: BaseException) -> VisaErrorKind:
    """Best-effort mapping of an exception to a :class:`VisaErrorKind`.

    Prefers authoritative signals (``pyvisa.errors.VisaIOError.error_code``)
    over message inspection.  Falls back to keyword heuristics for
    exceptions raised by the PyVISA backend stack (``LibraryError``,
    ``OSError`` from ASRL, etc.) or by third-party code wrapping VISA.
    """
    # Avoid importing pyvisa at module load — the classifier must
    # work even if pyvisa is not present (helps the unit tests).
    VisaIOError = _get_pyvisa_error_class("VisaIOError")
    LibraryError = _get_pyvisa_error_class("LibraryError")

    if LibraryError is not None and isinstance(exc, LibraryError):
        return VisaErrorKind.NO_VISA
    if VisaIOError is not None and isinstance(exc, VisaIOError):
        constants = _get_pyvisa_constants()
        code = getattr(exc, "error_code", None)
        if constants is not None and code is not None:
            if code == getattr(constants, "VI_ERROR_LIBRARY_NFOUND", None):
                return VisaErrorKind.NO_VISA
            if code == getattr(constants, "VI_ERROR_RSRC_NFOUND", None):
                return VisaErrorKind.NO_DEVICE
            if code == getattr(constants, "VI_ERROR_TMO", None):
                return VisaErrorKind.TIMEOUT
            asrl_codes = {
                getattr(constants, name, None)
                for name in ("VI_ERROR_ASRL_FRAMING",
                             "VI_ERROR_ASRL_OVERRUN",
                             "VI_ERROR_ASRL_PARITY")
            }
            asrl_codes.discard(None)
            if code in asrl_codes:
                return VisaErrorKind.TRANSPORT
        # Fall through to keyword heuristics on the message.

    msg = str(exc).lower()
    # Library-missing is the most important case to distinguish from
    # a configuration mistake — operators need to know "install X".
    if any(kw in msg for kw in (
            "library not loadable", "could not open visa library",
            "nivisa", "visa32.dll", "visa64.dll",
            "no visa library", "library not found",
            "could not find a visa implementation")):
        return VisaErrorKind.NO_VISA
    if any(kw in msg for kw in (
            "rsrc_nfound", "resource not found",
            "insufficient location information",
            "resource was not found", "unable to locate")):
        return VisaErrorKind.NO_DEVICE
    if "timeout" in msg or "timed out" in msg:
        return VisaErrorKind.TIMEOUT
    if any(kw in msg for kw in (
            "asrl", "serial", "com port", "port is already",
            "access is denied", "permission denied")):
        return VisaErrorKind.TRANSPORT
    if isinstance(exc, OSError):
        return VisaErrorKind.TRANSPORT
    return VisaErrorKind.UNKNOWN


def format_for_operator(err: BaseException, *,
                         context: Optional[str] = None) -> str:
    """Return a short operator-facing one-liner for ``err``.

    Accepts either a :class:`ClassifiedVisaError` (uses the tag it
    already carries) or a raw exception (classifies it first).  The
    ``context`` argument — when provided — prefixes the message with
    a short "what were we doing" tag (e.g. ``"K2000 connect"``).
    """
    if isinstance(err, ClassifiedVisaError):
        kind = err.kind
        original = err.original
        ctx = err.context if context is None else context
    else:
        kind = classify(err)
        original = err
        ctx = context or ""
    hint = REMEDIATION.get(kind, REMEDIATION[VisaErrorKind.UNKNOWN])
    prefix = f"{ctx}: " if ctx else ""
    return (f"{prefix}{kind.value} "
            f"— {type(original).__name__}: {original}  "
            f"({hint})")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _get_pyvisa_error_class(name: str):
    try:
        from pyvisa import errors  # type: ignore
    except Exception:
        return None
    return getattr(errors, name, None)


def _get_pyvisa_constants():
    try:
        from pyvisa import constants  # type: ignore
    except Exception:
        return None
    return constants


__all__ = [
    "VisaErrorKind",
    "ClassifiedVisaError",
    "REMEDIATION",
    "classify",
    "format_for_operator",
]
