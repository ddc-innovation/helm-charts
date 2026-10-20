#!/usr/bin/env python3
"""Verify the exact JDBC artifact inspected during the deployment."""
import hashlib
from pathlib import Path
import struct
import sys
import zipfile

EXPECTED='0353648eaa1c91e0f4020c959abf756bc866ffd583df22ae6b6f6e0cbd43eb44'


def verify(path):
    if hashlib.sha256(path.read_bytes()).hexdigest()!=EXPECTED:
        raise ValueError('Connector/J checksum differs from the inspected artifact')
    with zipfile.ZipFile(path) as archive:
        data=archive.read('com/mysql/cj/jdbc/Driver.class')
    magic,minor,major=struct.unpack('>IHH',data[:8])
    if magic!=0xCAFEBABE or major>52:
        raise ValueError('Driver class is not compatible with Java 8 bytecode')
    return minor,major


if __name__=='__main__':
    try:
        minor,major=verify(Path(sys.argv[1]))
        print('PASS: exact Connector/J 9.7.0 checksum and driver class')
        print('Driver class major version:',major)
        print('Class loading does not prove database/TLS connectivity; run runtime tests')
    except (IndexError,KeyError,OSError,ValueError,zipfile.BadZipFile,struct.error) as exc:
        raise SystemExit('FAIL: '+str(exc))
