"""Inherited native owner channel for the shared APK Runtime.

No pathname/reconnect protocol and no imported authority document. FD 3 is the
single inherited SOCK_SEQPACKET lease, never inherited by startup helpers. The
current native producer refuses launch until C/A and helper closure exist.
"""

from __future__ import annotations

import socket
import struct

FRAME = struct.Struct("!4sIII")
MAGIC = b"SCH1"
MAX_SEQUENCE = 0xFFFFFFFF


class OwnerLease:
    def __init__(self) -> None:
        self.channel = socket.socket(fileno=3)
        self.channel.set_inheritable(False)
        self.channel.settimeout(2)
        if (
            self.channel.family != socket.AF_UNIX
            or self.channel.type != socket.SOCK_SEQPACKET
        ):
            self.channel.close()
            raise ValueError("APK owner lease is not a private sequenced channel")
        self.sequence = 0

    def exchange(self, operation: int) -> int:
        if operation not in (1, 2, 3) or self.sequence == MAX_SEQUENCE:
            raise ValueError("APK owner protocol refused")
        self.sequence += 1
        self.channel.sendall(FRAME.pack(MAGIC, 1, self.sequence, operation))
        raw, ancillary, flags, _address = self.channel.recvmsg(FRAME.size, 0)
        if (
            ancillary
            or flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC)
            or len(raw) != FRAME.size
        ):
            raise ValueError("APK owner response incomplete")
        magic, version, sequence, result = FRAME.unpack(raw)
        if magic != MAGIC or version != 1 or sequence != self.sequence:
            raise ValueError("APK owner response binding refused")
        return result

    def close(self) -> None:
        self.channel.close()


def require_launch() -> None:
    # No public flag, environment value or parsed result can supply C/A. Even a
    # peer sending an invented grant cannot bypass this closed production gate.
    lease = OwnerLease()
    try:
        lease.exchange(1)
        raise ValueError("APK source admission pending native C/A and helper closure")
    finally:
        lease.close()
