#!/usr/bin/env python3
"""Small real-protocol fixtures for FTP(S), SFTP, IMAPS, POP3S and MCP tests."""

from __future__ import annotations

import io
import socket
import socketserver
import ssl
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path, PurePosixPath

import paramiko
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings


USERNAME = "fixture"
PASSWORD = "Fixture@123456"
FILES = {
    "/fact.txt": b"NexusOne supports REST, RSS, S3 and knowledge graph retrieval.\n",
    "/nested/note.md": b"# Fixture\nNexusOne is positioned as an enterprise knowledge product.\n",
}


class ReusableTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class FTPHandler(socketserver.StreamRequestHandler):
    passive: socket.socket | None = None
    tls: ssl.SSLContext | None = None
    data_tls = False

    def reply(self, value: str) -> None:
        self.wfile.write((value + "\r\n").encode("utf-8"))
        self.wfile.flush()

    def passive_socket(self) -> socket.socket:
        if self.passive is None:
            raise RuntimeError("FTP passive mode was not initialized")
        value, self.passive = self.passive, None
        return value

    def accept_data(self) -> socket.socket:
        listener = self.passive_socket()
        connection, _ = listener.accept()
        listener.close()
        if self.data_tls and self.tls:
            connection = self.tls.wrap_socket(connection, server_side=True)
        return connection

    def handle(self) -> None:
        self.reply("220 Chuanshen FTP fixture")
        while raw := self.rfile.readline(4096):
            command, _, argument = raw.decode("utf-8", errors="replace").strip().partition(" ")
            command = command.upper()
            if command == "AUTH" and argument.upper() == "TLS" and self.tls:
                self.reply("234 Proceed with negotiation")
                wrapped = self.tls.wrap_socket(self.request, server_side=True)
                self.connection = self.request = wrapped
                self.rfile = wrapped.makefile("rb", self.rbufsize)
                self.wfile = wrapped.makefile("wb", self.wbufsize)
            elif command == "PBSZ":
                self.reply("200 PBSZ=0")
            elif command == "PROT":
                self.data_tls = argument.upper() == "P"
                self.reply("200 Protection level accepted")
            elif command == "USER":
                self.reply("331 Password required")
            elif command == "PASS":
                self.reply("230 Login successful" if argument == PASSWORD else "530 Login incorrect")
            elif command in {"TYPE", "OPTS", "NOOP"}:
                self.reply("200 OK")
            elif command in {"PASV", "EPSV"}:
                if self.passive:
                    self.passive.close()
                self.passive = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.passive.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                self.passive.bind(("0.0.0.0", 0))
                self.passive.listen(1)
                port = self.passive.getsockname()[1]
                if command == "EPSV":
                    self.reply(f"229 Entering Extended Passive Mode (|||{port}|)")
                else:
                    host = self.request.getsockname()[0].split(".")
                    self.reply(f"227 Entering Passive Mode ({','.join(host)},{port // 256},{port % 256})")
            elif command == "MLSD":
                remote = "/" + argument.strip("/") if argument.strip("/") else "/"
                prefix = remote.rstrip("/") + "/"
                entries: dict[str, tuple[str, int]] = {}
                for name, body in FILES.items():
                    if not name.startswith(prefix):
                        continue
                    relative = name[len(prefix):]
                    first = relative.split("/", 1)[0]
                    entries[first] = ("dir", 0) if "/" in relative else ("file", len(body))
                self.reply("150 Opening data connection")
                connection = self.accept_data()
                try:
                    for name, (kind, size) in sorted(entries.items()):
                        connection.sendall(f"type={kind};size={size}; {name}\r\n".encode())
                    if isinstance(connection, ssl.SSLSocket):
                        connection.unwrap()
                finally:
                    connection.close()
                self.reply("226 Transfer complete")
            elif command == "RETR":
                target = "/" + argument.strip("/")
                body = FILES.get(target)
                if body is None:
                    self.reply("550 File unavailable")
                    continue
                self.reply(f"150 Opening binary mode data connection ({len(body)} bytes)")
                connection = self.accept_data()
                try:
                    connection.sendall(body)
                    if isinstance(connection, ssl.SSLSocket):
                        connection.unwrap()
                finally:
                    connection.close()
                self.reply("226 Transfer complete")
            elif command == "QUIT":
                self.reply("221 Goodbye")
                break
            else:
                self.reply("502 Command not implemented")


class FTPSHandler(FTPHandler):
    pass


class SFTPAuth(paramiko.ServerInterface):
    def check_auth_password(self, username: str, password: str):
        return paramiko.AUTH_SUCCESSFUL if username == USERNAME and password == PASSWORD else paramiko.AUTH_FAILED

    def get_allowed_auths(self, username: str) -> str:
        return "password"

    def check_channel_request(self, kind: str, chanid: int):
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED


class ReadOnlyHandle(paramiko.SFTPHandle):
    def __init__(self, body: bytes):
        super().__init__(flags=0)
        self.body = body

    def read(self, offset: int, length: int):
        return self.body[offset:offset + length]


class MemorySFTP(paramiko.SFTPServerInterface):
    @staticmethod
    def _normalize(path: str) -> str:
        normalized = str(PurePosixPath("/" + path.lstrip("/")))
        return normalized if normalized.startswith("/") else "/" + normalized

    def list_folder(self, path: str):
        remote = self._normalize(path)
        prefix = remote.rstrip("/") + "/"
        entries: dict[str, tuple[bool, int]] = {}
        for name, body in FILES.items():
            if not name.startswith(prefix):
                continue
            relative = name[len(prefix):]
            first = relative.split("/", 1)[0]
            entries[first] = (True, 0) if "/" in relative else (False, len(body))
        result = []
        for name, (directory, size) in sorted(entries.items()):
            attr = paramiko.SFTPAttributes()
            attr.filename = name
            attr.st_mode = 0o040755 if directory else 0o100644
            attr.st_size = size
            result.append(attr)
        return result

    def stat(self, path: str):
        remote = self._normalize(path)
        attr = paramiko.SFTPAttributes()
        if remote in FILES:
            attr.st_mode, attr.st_size = 0o100644, len(FILES[remote])
            return attr
        prefix = remote.rstrip("/") + "/"
        if any(name.startswith(prefix) for name in FILES):
            attr.st_mode, attr.st_size = 0o040755, 0
            return attr
        return paramiko.SFTP_NO_SUCH_FILE

    lstat = stat

    def open(self, path: str, flags: int, attr):
        body = FILES.get(self._normalize(path))
        return ReadOnlyHandle(body) if body is not None else paramiko.SFTP_NO_SUCH_FILE


def run_sftp() -> None:
    key = paramiko.RSAKey.generate(2048)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("0.0.0.0", 2222))
    listener.listen(20)
    while True:
        connection, _ = listener.accept()

        def serve(client: socket.socket) -> None:
            transport = paramiko.Transport(client)
            transport.add_server_key(key)
            transport.set_subsystem_handler("sftp", paramiko.SFTPServer, MemorySFTP)
            try:
                transport.start_server(server=SFTPAuth())
                channel = transport.accept(20)
                while channel is not None and transport.is_active():
                    transport.join(1)
            finally:
                transport.close()

        threading.Thread(target=serve, args=(connection,), daemon=True).start()


def fixture_email() -> bytes:
    message = EmailMessage()
    message["From"] = "sender@example.test"
    message["To"] = "knowledge@example.test"
    message["Subject"] = "NexusOne product facts"
    message["Message-ID"] = "<nexusone-fixture@example.test>"
    message.set_content("NexusOne is an enterprise knowledge product with multimodal ingestion.")
    message.add_attachment(
        b"NexusOne email attachment fact",
        maintype="text",
        subtype="plain",
        filename="attachment.txt",
    )
    return message.as_bytes()


class POP3Handler(socketserver.StreamRequestHandler):
    body = fixture_email()

    def reply(self, value: bytes) -> None:
        self.wfile.write(value + b"\r\n")
        self.wfile.flush()

    def handle(self) -> None:
        self.reply(b"+OK Chuanshen POP3 fixture ready")
        while raw := self.rfile.readline(4096):
            command, _, argument = raw.decode(errors="replace").strip().partition(" ")
            command = command.upper()
            if command == "USER":
                self.reply(b"+OK")
            elif command == "PASS":
                self.reply(b"+OK" if argument == PASSWORD else b"-ERR")
            elif command == "LIST":
                self.reply(f"+OK 1 messages ({len(self.body)} octets)".encode())
                self.reply(f"1 {len(self.body)}".encode())
                self.reply(b".")
            elif command == "RETR" and argument == "1":
                self.reply(f"+OK {len(self.body)} octets".encode())
                for line in self.body.splitlines():
                    self.reply((b"." + line) if line.startswith(b".") else line)
                self.reply(b".")
            elif command == "QUIT":
                self.reply(b"+OK goodbye")
                break
            else:
                self.reply(b"-ERR unsupported")


class IMAPHandler(socketserver.StreamRequestHandler):
    body = fixture_email()

    def send_line(self, value: bytes) -> None:
        self.wfile.write(value + b"\r\n")
        self.wfile.flush()

    def handle(self) -> None:
        self.send_line(b"* OK [CAPABILITY IMAP4rev1] Chuanshen IMAP fixture ready")
        while raw := self.rfile.readline(8192):
            line = raw.decode("utf-8", errors="replace").strip()
            tag, _, rest = line.partition(" ")
            command, _, arguments = rest.partition(" ")
            command = command.upper()
            if command == "CAPABILITY":
                self.send_line(b"* CAPABILITY IMAP4rev1")
                self.send_line(f"{tag} OK CAPABILITY completed".encode())
            elif command == "LOGIN":
                self.send_line(f"{tag} OK LOGIN completed".encode())
            elif command in {"SELECT", "EXAMINE"}:
                self.send_line(b"* FLAGS (\\Seen)")
                self.send_line(b"* 1 EXISTS")
                self.send_line(b"* 0 RECENT")
                self.send_line(f"{tag} OK [READ-WRITE] SELECT completed".encode())
            elif command == "SEARCH":
                self.send_line(b"* SEARCH 1")
                self.send_line(f"{tag} OK SEARCH completed".encode())
            elif command == "FETCH":
                self.wfile.write(f"* 1 FETCH (RFC822 {{{len(self.body)}}}\r\n".encode())
                self.wfile.write(self.body + b")\r\n")
                self.send_line(f"{tag} OK FETCH completed".encode())
            elif command == "LOGOUT":
                self.send_line(b"* BYE Logging out")
                self.send_line(f"{tag} OK LOGOUT completed".encode())
                break
            else:
                self.send_line(f"{tag} BAD unsupported command {command} {arguments}".encode())


def tls_context() -> ssl.SSLContext:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "protocol-fixture")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("protocol-fixture")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    temporary = Path(tempfile.mkdtemp(prefix="chuanshen-pop3-"))
    cert_path, key_path = temporary / "cert.pem", temporary / "key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption())
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    return context


def start_protocol_servers() -> None:
    ftp = ReusableTCPServer(("0.0.0.0", 2121), FTPHandler)
    ftps = ReusableTCPServer(("0.0.0.0", 2990), FTPSHandler)
    mail_tls = tls_context()
    FTPSHandler.tls = mail_tls
    pop3 = ReusableTCPServer(("0.0.0.0", 1995), POP3Handler)
    pop3.socket = mail_tls.wrap_socket(pop3.socket, server_side=True)
    imap = ReusableTCPServer(("0.0.0.0", 1993), IMAPHandler)
    imap.socket = mail_tls.wrap_socket(imap.socket, server_side=True)
    threading.Thread(target=ftp.serve_forever, daemon=True).start()
    threading.Thread(target=ftps.serve_forever, daemon=True).start()
    threading.Thread(target=pop3.serve_forever, daemon=True).start()
    threading.Thread(target=imap.serve_forever, daemon=True).start()
    threading.Thread(target=run_sftp, daemon=True).start()


mcp = FastMCP(
    "传神协议测试",
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(
        allowed_hosts=["protocol-fixture:8095", "localhost:8095", "127.0.0.1:8095"]
    ),
)


@mcp.resource("fixture://nexusone")
def nexusone_resource() -> str:
    """Return a stable knowledge fact used by connector acceptance tests."""
    return "NexusOne supports multimodal ingestion and hybrid retrieval."


def main() -> None:
    start_protocol_servers()
    uvicorn.run(mcp.streamable_http_app(), host="0.0.0.0", port=8095, log_level="warning")


if __name__ == "__main__":
    main()
