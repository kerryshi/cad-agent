"""Implicit-FTPS client for the P2S, with the two quirks vsftpd requires.

Two things break a stock ftplib client against this printer:
  1. IMPLICIT TLS on :990 — the socket is encrypted from the first byte,
     rather than upgrading via AUTH TLS. ftplib only speaks explicit FTPS.
  2. TLS SESSION REUSE — vsftpd is configured with require_ssl_reuse, so the
     data connection must resume the control connection's TLS session or it
     answers "522 SSL connection failed: session reuse required". ftplib
     negotiates a fresh session for each transfer, so every LIST/STOR fails
     even though login succeeded.

Symptom worth recognising: login works and directory operations do not.
That is (2), not a permissions or path problem.

The printer presents a self-signed certificate, so verification is disabled —
acceptable here because this is a LAN-local device addressed by IP, and the
access code is the actual authenticator.
"""

from __future__ import annotations

import ftplib
import ssl

FTPS_PORT = 990
FTP_USER = "bblp"  # fixed by Bambu firmware; the access code is the password


class _ReusedSSLSocket(ssl.SSLSocket):
    """vsftpd closes data sockets itself; unwrapping again raises."""

    def unwrap(self):  # noqa: D102
        pass


class PrinterFTPS(ftplib.FTP_TLS):
    """FTP_TLS that is implicit-on-connect and reuses the control session."""

    def __init__(self, host: str, access_code: str, timeout: float = 30.0):
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        self._sock = None
        super().__init__(context=context)
        self.connect(host=host, port=FTPS_PORT, timeout=timeout)
        self.login(FTP_USER, access_code)
        self.prot_p()

    @property
    def sock(self):
        return self._sock

    @sock.setter
    def sock(self, value):
        """Wrap the control socket in TLS immediately — implicit FTPS."""
        if value is not None and not isinstance(value, ssl.SSLSocket):
            value = self.context.wrap_socket(value, server_hostname=self.host)
        self._sock = value

    def ntransfercmd(self, cmd, rest=None):
        """Resume the control connection's TLS session on the data channel."""
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(
                conn, server_hostname=self.host, session=self.sock.session
            )
            conn.__class__ = _ReusedSSLSocket
        return conn, size

    def listdir(self, path: str = "/") -> list[str]:
        self.cwd(path)
        entries: list[str] = []
        self.retrlines("LIST", entries.append)
        return entries
