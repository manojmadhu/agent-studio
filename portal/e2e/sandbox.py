#!/usr/bin/env python3
"""
Loads the preview build inside a sandboxed iframe (sandbox="allow-scripts", opaque origin: no Web Storage,
CORS-mode fetches blocked) the way the static preview host does, and checks the app works there.
Usage: npm run build && npm run preview  →  python3 e2e/sandbox.py [app_url]
"""
import http.server
import os
import sys
import threading

from playwright.sync_api import sync_playwright

APP = sys.argv[1] if len(sys.argv) > 1 else 'http://localhost:4173/'
HOST_PAGE = f'<!doctype html><html><body style="margin:0"><iframe id="f" sandbox="allow-scripts" src="{APP}#builder-pl_ap" style="width:1400px;height:860px;border:0"></iframe></body></html>'


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = HOST_PAGE.encode() if self.path in ('/', '/index.html') else b''
        self.send_response(200 if body else 204)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def main():
    srv = http.server.HTTPServer(('127.0.0.1', 4301), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    ok = True
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path='/usr/bin/google-chrome', args=['--no-sandbox'])
        pg = b.new_page(viewport={'width': 1400, 'height': 860})
        errors = []
        pg.on('console', lambda m: errors.append(m.text) if m.type == 'error' else None)
        pg.goto('http://127.0.0.1:4301/')
        f = pg.frame_locator('#f')
        f.locator('.react-flow__node').first.wait_for(timeout=15000)
        n = f.locator('.react-flow__node').count()
        print(('PASS' if n > 20 else 'FAIL'), f'builder renders in a sandboxed iframe ({n} nodes)')
        ok &= n > 20
        f.locator('a[title="Connections"]').click()
        f.locator('tbody tr').first.wait_for()
        print('PASS navigation with plain hashes inside the sandbox')
        pg.screenshot(path=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'shots', 'sandboxed_iframe.png'))
        print(('PASS' if not errors else 'FAIL'), 'no console errors', errors[:3])
        ok &= not errors
        b.close()
    srv.shutdown()
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
