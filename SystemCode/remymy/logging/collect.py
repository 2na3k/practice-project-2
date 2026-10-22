"""Keep the output of every container of the Compose project in a log file per hour: LOG_DIR/<service>/YYYY-MM-DD_HH.log.

The log-collector service runs this. It reads each container's stdout and stderr from the Docker Engine API (the
socket at DOCKER_SOCKET), as `docker compose logs` does, so it covers every service: the ones built here and the
upstream images (Redis, Postgres, ClickHouse, MinIO, Langfuse) alike, and a container that exits at once, like a
failed api-migrate. A line goes to the file of the hour (UTC) Docker received it in; the lines are written as the
service printed them. On each new hour, files older than LOG_RETENTION_HOURS are deleted.

  COMPOSE_PROJECT      the project whose containers are followed (default remymy)
  LOG_DIR              default /var/log/remymy (the ./logs bind mount in compose)
  LOG_RETENTION_HOURS  default 168 (a week); 0 keeps every file
  DOCKER_SOCKET        default /var/run/docker.sock

Only the standard library, so it runs on the stock python image."""
from __future__ import annotations

import http.client
import json
import os
import socket
import struct
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote

PROJECT = os.getenv("COMPOSE_PROJECT", "remymy")
LOG_DIR = Path(os.getenv("LOG_DIR", "/var/log/remymy"))
RETENTION_HOURS = int(os.getenv("LOG_RETENTION_HOURS", "168"))
DOCKER_SOCKET = os.getenv("DOCKER_SOCKET", "/var/run/docker.sock")
POLL_SECONDS = 1.0
SELF = "log-collector"


def log(message: str) -> None:
    print(f"log-collector: {message}", file=sys.stderr, flush=True)


class DockerConnection(http.client.HTTPConnection):
    """HTTP to the Docker Engine API over its unix socket."""

    def __init__(self) -> None:
        super().__init__("docker")

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(DOCKER_SOCKET)


def docker_json(path: str) -> object:
    connection = DockerConnection()
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        body = response.read()
        if response.status != 200:
            raise RuntimeError(f"GET {path}: {response.status} {body[:200]!r}")
        return json.loads(body)
    finally:
        connection.close()


class HourlyFiles:
    """The open file of each service's current hour, shared by the containers of a service."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.files: dict[str, tuple[str, object]] = {}
        self.pruned_hour = ""

    def write(self, service: str, hour: str, line: bytes) -> None:
        with self.lock:
            current = self.files.get(service)
            if current is None or current[0] != hour:
                if current is not None:
                    current[1].close()
                folder = LOG_DIR / service
                folder.mkdir(parents=True, exist_ok=True)
                self.files[service] = current = (hour, open(folder / f"{hour}.log", "ab"))
            current[1].write(line)
            current[1].flush()
            if hour != self.pruned_hour:
                self.pruned_hour = hour
                self.prune()

    def prune(self) -> None:
        if RETENTION_HOURS <= 0:
            return
        oldest = time.time() - RETENTION_HOURS * 3600
        for path in LOG_DIR.glob("*/*.log"):
            try:
                if path.stat().st_mtime < oldest:
                    path.unlink()
            except OSError:
                pass


FILES = HourlyFiles()


def lines(stream, tty: bool):
    """The lines of a container's log stream. Without a TTY, Docker frames each chunk with an 8-byte header (the
    stream, then the length); with one, the bytes come as they are."""
    buffer = b""
    while True:
        if tty:
            chunk = stream.read1(65536) if hasattr(stream, "read1") else stream.read(65536)
            if not chunk:
                break
        else:
            header = stream.read(8)
            if len(header) < 8:
                break
            chunk = stream.read(struct.unpack(">I", header[4:])[0])
        buffer += chunk
        *complete, buffer = buffer.split(b"\n")
        yield from complete
    if buffer:
        yield buffer


def follow(container_id: str, service: str, tty: bool, since: float, last: dict[str, str]) -> None:
    """Write the container's output from since on, until it stops; last keeps the newest timestamp written, so a
    restart goes on after it without writing a line twice."""
    path = (f"/containers/{container_id}/logs?follow=1&stdout=1&stderr=1&timestamps=1"
            f"&since={since:.9f}")
    connection = DockerConnection()
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        if response.status != 200:
            log(f"cannot read the logs of {service}: {response.status}")
            return
        for line in lines(response, tty):
            # "2026-10-22T04:55:22.344123456Z message"
            stamp, _, message = line.partition(b" ")
            text = stamp.decode("ascii", "replace")
            if len(text) < 13 or text <= last.get(container_id, ""):
                continue
            last[container_id] = text
            FILES.write(service, f"{text[:10]}_{text[11:13]}", message + b"\n")
    except OSError as error:
        log(f"lost the logs of {service}: {error}")
    finally:
        connection.close()


def main() -> None:
    started = time.time()
    followers: dict[str, threading.Thread] = {}
    last: dict[str, str] = {}
    filters = quote(json.dumps({"label": [f"com.docker.compose.project={PROJECT}"]}))
    log(f"writing the logs of the {PROJECT} containers to {LOG_DIR}/<service>/YYYY-MM-DD_HH.log")
    while True:
        try:
            containers = docker_json(f"/containers/json?all=1&filters={filters}")
        except (OSError, RuntimeError) as error:
            log(f"cannot list the containers: {error}")
            time.sleep(5)
            continue
        for container in containers:
            service = container["Labels"].get("com.docker.compose.service", "")
            container_id = container["Id"]
            if not service or service == SELF:
                continue
            thread = followers.get(container_id)
            if thread is not None and (thread.is_alive() or container["State"] != "running"):
                continue
            if thread is None:
                # all of a container made since this started; of an older one, only what it prints from now, as
                # an earlier run of this wrote the rest
                since = 0.0 if container["Created"] >= started - 5 else started
            else:
                # started again: on after the last line written
                since = 0.0
            try:
                tty = bool(docker_json(f"/containers/{container_id}/json")["Config"]["Tty"])
            except (OSError, RuntimeError, KeyError):
                continue
            followers[container_id] = threading.Thread(
                target=follow, args=(container_id, service, tty, since, last), daemon=True,
                name=f"follow-{service}")
            followers[container_id].start()
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
