"""Errors raised by the EBIC_ADC Modbus request layer.

Two kinds of failure are named here. :class:`UnexpectedResponse` means the
device answered, but the answer cannot belong to the request: a different PDU
type, a wrong register count, or an echo that does not match the write. A
transport failure keeps its own type from ``scietex.hal.serial`` and is retried
only where the request layer says so.

The capture errors describe the higher-level acquisition sequence rather than a
single frame: :class:`CaptureTimeout` means the buffer did not become ready in
time, and :class:`CaptureFailed` means the device reported that it stopped
without producing one.

Classes:
    UnexpectedResponse: A response cannot belong to its request.
    CaptureTimeout: A capture did not finish within its deadline.
    CaptureFailed: A capture stopped without producing a buffer.
"""


class UnexpectedResponse(OSError):
    """Raised when a response cannot belong to the request it answers.

    Args:
        subject: What the response was read for, such as a register range.
        detail: What is wrong, such as the PDU type or the register count.

    Attributes:
        subject: The subject the message names.
    """

    def __init__(self, subject: str, detail: str) -> None:
        self.subject = subject
        super().__init__(f"Unexpected response for {subject}: {detail}")


class CaptureTimeout(RuntimeError):
    """Raised when a capture does not become ready before its deadline.

    Args:
        timeout_s: The deadline the capture exceeded, in seconds.

    Attributes:
        timeout_s: The deadline the capture exceeded, in seconds.
    """

    def __init__(self, timeout_s: float) -> None:
        self.timeout_s = timeout_s
        super().__init__(f"capture did not finish within {timeout_s:.3f} s")


class CaptureFailed(RuntimeError):
    """Raised when a capture stopped without producing a buffer.

    Args:
        reason: Why the capture stopped, as the device reported it.

    Attributes:
        reason: Why the capture stopped.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"capture failed: {reason}")
