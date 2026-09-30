from email.header import Header

from src.inbox.inbox_monitor import _decode


def test_decode_handles_unknown_8bit_without_crashing():
    value = Header(b"caf\x82", "unknown-8bit").__str__()
    assert _decode(value)


def test_decode_handles_utf8_header():
    assert _decode("=?utf-8?b?Sm9i?=") == "Job"
