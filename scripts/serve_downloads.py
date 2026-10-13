#!/usr/bin/env python3
"""
Serve simulation artifacts (report markdown, profiles, logs) for download
from an IP address, restricted to a Tailscale address by default.

Usage:
    python scripts/serve_downloads.py [--host 100.127.45.60] [--port 8788] \
        [--dir backend/uploads/simulations]

Security notes (AGENTS.md):
- binds 127.0.0.1 only unless --host is an explicit tailscale address
- no shell: stdlib http.server only; serves a fixed file allowlist
- path is resolved and must stay under --dir
"""

import argparse
import http.server
import ipaddress
import os
import socketserver
import urllib.parse
import webbrowser

SIM_DEFAULT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           'backend', 'uploads', 'simulations')
ALLOW = {'.md', '.json', '.csv', '.jsonl', '.log', '.txt', '.html'}


def is_tailscale(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip) in ipaddress.ip_network('100.64.0.0/10')
    except ValueError:
        return False


class Handler(http.server.SimpleHTTPRequestHandler):
    root = SIM_DEFAULT

    def translate_path(self, path):
        rel = urllib.parse.unquote(self.path.split('?', 1)[0].split('#', 1)[0]).strip('/')
        full = os.path.realpath(os.path.join(self.root, rel))
        root_real = os.path.realpath(self.root)
        if not (full == root_real or full.startswith(root_real + os.sep)):
            return None
        if os.path.isdir(full):
            return None
        if os.path.splitext(full)[1].lower() not in ALLOW:
            return None
        return full if os.path.isfile(full) else None

    def do_GET(self):
        f = self.translate_path(self.path)
        if f is None:
            self.send_error(404, 'not found (directories/blocked)')
            return
        try:
            with open(f, 'rb') as fh:
                data = fh.read()
        except OSError:
            self.send_error(404, 'not found')
            return
        name = os.path.basename(f)
        self.send_response(200)
        self.send_header('Content-Type', 'text/markdown; charset=utf-8'
                         if name.endswith('.md') or name.endswith('.html') else 'application/octet-stream')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Content-Disposition', f'attachment; filename="{name}"')
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        print(f"[downloads] {self.address_string()} {fmt % args}")


def build_index(root: str) -> str:
    rows = []
    for sim in sorted(os.listdir(root)):
        d = os.path.join(root, sim)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if os.path.splitext(fn)[1].lower() not in ALLOW:
                continue
            p = os.path.join(d, fn)
            rows.append(f'<li><a href="/{sim}/{fn}">{sim}/{fn}</a> '
                        f'({os.path.getsize(p)/1024:.0f} KB)</li>')
    return ('<html><head><title>MiroFish simulation downloads</title></head><body>'
            '<h1>MiroFish simulation downloads</h1><ul>' + ''.join(rows) + '</ul></body></html>')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--host', default=None,
                   help='bind address (default: first tailscale IP; falls back to loopback)')
    p.add_argument('--port', type=int, default=8788)
    p.add_argument('--dir', default=SIM_DEFAULT)
    p.add_argument('--allow-lan', action='store_true',
                   help='allow binding a non-tailscale address (explicit opt-in)')
    args = p.parse_args()

    root = os.path.realpath(args.dir)
    Handler.root = root

    import socket
    host = args.host
    if not host:
        host = None
        for line in os.popen('ifconfig').read().splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0].startswith('inet') and is_tailscale(parts[1]):
                host = parts[1]
                break
        if not host:
            print('no tailscale IP found — binding 127.0.0.1. '
                  'Pass --host explicitly to use another address.')
            host = '127.0.0.1'

    if not is_tailscale(host) and not args.allow_lan:
        print(f'refusing to bind {host} (not a tailscale IP). '
              f'Use --allow-lan to override, or pass a 100.64.0.0/10 address.')
        raise SystemExit(2)

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    import tempfile
    idx_path = os.path.join(tempfile.gettempdir(), 'mirofish_downloads_index.html')
    with open(idx_path, 'w') as fh:
        fh.write(build_index(root))

    class IdxHandler(Handler):
        def do_GET(self):
            if self.path in ('/', '/index.html'):
                with open(idx_path, 'rb') as fh:
                    data = fh.read()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            return super().do_GET()

    with Server((host, args.port), IdxHandler) as srv:
        url = f'http://{host}:{args.port}/'
        print(f'serving {root} on {url}')
        print('  download page: ' + url)
        print('  example: ' + url + 'sim_masked_s1_n100/REPORT_masked_personas_final_2026-10-11.md')
        try:
            webbrowser.open(url)
        except Exception:
            pass
        srv.serve_forever()


if __name__ == '__main__':
    main()