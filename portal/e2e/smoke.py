#!/usr/bin/env python3
"""
Browser smoke test for the Doc Intelligence Studio portal (mock mode).

Usage:  npm run build && npm run preview   (serves dist on :4173)
        python3 e2e/smoke.py [base_url]

Checks: every screen loads with zero console errors; builder gestures (palette drag, connect,
plug a model, slot menu, delete an edge, undo); a test run completes and shows extracted data;
a connection is created and only the secret hint is ever shown. Screenshots → e2e/shots/.
"""
import os
import sys

from playwright.sync_api import sync_playwright, expect

BASE = sys.argv[1] if len(sys.argv) > 1 else 'http://localhost:4173/'
SHOTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'shots')
CHROME = '/usr/bin/google-chrome'
SECRET = 'sk-live-TOPSECRET-9f8e7d6c5b4aQz1x'
os.makedirs(SHOTS, exist_ok=True)

SCREENS = ['pipelines', 'builder-pl_ap', 'review', 'review-rv1', 'runs', 'models', 'skills', 'tools', 'workers', 'policies', 'connections', 'settings']
results = []


def check(name, cond, detail=''):
    results.append((name, bool(cond), detail))
    print(('PASS ' if cond else 'FAIL ') + name + (f' — {detail}' if detail else ''))


def shot(page, name):
    page.screenshot(path=os.path.join(SHOTS, name + '.png'))


def center(page, selector):
    b = page.locator(selector).first.bounding_box()
    return b['x'] + b['width'] / 2, b['y'] + b['height'] / 2


def drag_mouse(page, x1, y1, x2, y2, steps=18):
    page.mouse.move(x1, y1)
    page.mouse.down()
    page.mouse.move(x1 + 6, y1 + 4, steps=3)
    page.mouse.move(x2, y2, steps=steps)
    page.wait_for_timeout(80)
    page.mouse.up()
    page.wait_for_timeout(250)


def palette_drop(page, label, x, y):
    """Open the step list with the toolbar + and HTML5-drag a step onto the canvas at (x, y)."""
    if not page.locator('#pal.open').count():
        page.click('.ctl .addbtn')
        page.wait_for_timeout(250)
    src = page.locator('.pit', has_text=label).first
    src.scroll_into_view_if_needed()
    page.evaluate(
        """([label, x, y]) => {
            const src = [...document.querySelectorAll('.pit')].find(b => b.textContent.includes(label));
            const dt = new DataTransfer();
            src.dispatchEvent(new DragEvent('dragstart', {bubbles: true, dataTransfer: dt}));
            const tgt = document.elementFromPoint(x, y);
            tgt.dispatchEvent(new DragEvent('dragover', {bubbles: true, cancelable: true, dataTransfer: dt, clientX: x, clientY: y}));
            tgt.dispatchEvent(new DragEvent('drop', {bubbles: true, cancelable: true, dataTransfer: dt, clientX: x, clientY: y}));
        }""",
        [label, x, y],
    )
    page.wait_for_timeout(400)


def counts(page):
    return page.evaluate("() => ({nodes: document.querySelectorAll('.react-flow__node').length, edges: document.querySelectorAll('.react-flow__edge').length})")


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME, args=['--no-sandbox'])
        ctx = browser.new_context(viewport={'width': 1440, 'height': 900})
        page = ctx.new_page()
        errors = []
        page.on('console', lambda m: errors.append(f'{m.type}: {m.text}') if m.type == 'error' else None)
        page.on('pageerror', lambda e: errors.append(f'pageerror: {e}'))

        # 1 ── every screen loads without console errors
        for s in SCREENS:
            before = len(errors)
            page.goto(BASE + '#' + s)
            page.wait_for_selector('#content')
            page.wait_for_timeout(1200)
            shot(page, f'screen_{s}')
            check(f'screen {s} loads without console errors', len(errors) == before, '; '.join(errors[before:]))

        # 2 ── builder gestures on a new pipeline
        page.goto(BASE + '#pipelines')
        page.click('button:has-text("New pipeline")')
        page.fill('input[name=n]', 'Smoke test pipeline')
        page.select_option('select[name=t]', 'intake.upload')
        page.click('[data-ok]')
        page.wait_for_selector('.react-flow__node')
        page.wait_for_timeout(800)
        check('new pipeline opens in the builder with its intake', counts(page)['nodes'] == 1)

        # 2a ── side panels hidden by default; + on an empty canvas; settings follow the selection
        vis = lambda sel: page.evaluate("(s) => { const e = document.querySelector(s); return !!e && getComputedStyle(e).visibility === 'visible' && e.getBoundingClientRect().right > 0 && e.getBoundingClientRect().left < innerWidth; }", sel)
        page.wait_for_timeout(300)
        check('step list and settings panel are hidden when the builder opens', not vis('#pal') and not vis('#insp'))
        nav_bar = page.evaluate("() => { const a = document.querySelector('.nav a.on'); return a ? getComputedStyle(a).boxShadow + '|' + getComputedStyle(a).borderLeftWidth : 'none'; }")
        check('active navigation item has no left accent bar', nav_bar.split('|')[0] == 'none' and nav_bar.split('|')[1] in ('0px', ''), nav_bar)
        only = page.evaluate("() => document.querySelector('.react-flow__node').dataset.id")
        page.click(f'.react-flow__node[data-id="{only}"] .nd')
        page.wait_for_timeout(300)
        check('clicking a step shows its settings', vis('#insp'))
        page.keyboard.press('Delete')
        page.wait_for_timeout(400)
        check('settings hide when the step is deleted', not vis('#insp'))
        check('empty canvas shows a + button', counts(page)['nodes'] == 0 and page.locator('.cempty .addfirst').is_visible())
        shot(page, 'builder_empty_plus')
        page.click('.cempty .addfirst')
        page.wait_for_timeout(300)
        check('+ opens the step list', vis('#pal'))
        shot(page, 'builder_step_list')
        page.click('#pal .pit:has-text("Upload portal")')
        page.wait_for_timeout(400)
        check('choosing a step adds it and closes the list', counts(page)['nodes'] == 1 and not vis('#pal'))
        only = page.evaluate("() => document.querySelector('.react-flow__node').dataset.id")
        page.click(f'.react-flow__node[data-id="{only}"] .nd')
        page.wait_for_timeout(300)
        shot(page, 'builder_settings_open')
        cvb = page.locator('#cv').bounding_box()
        page.mouse.click(cvb['x'] + cvb['width'] / 2, cvb['y'] + cvb['height'] - 70)
        page.wait_for_timeout(300)
        check('clicking outside hides the settings', not vis('#insp'))
        page.click('.ctl .addbtn')
        page.wait_for_timeout(250)
        page.mouse.click(cvb['x'] + cvb['width'] / 2, cvb['y'] + cvb['height'] - 70)
        page.wait_for_timeout(300)
        check('clicking outside hides the step list', not vis('#pal'))

        cv = page.locator('#cv').bounding_box()
        palette_drop(page, 'Text recognition', cv['x'] + 520, cv['y'] + 330)
        check('drag a step from the palette onto the canvas', counts(page)['nodes'] == 2)
        palette_drop(page, 'Extract fields', cv['x'] + 520, cv['y'] + 520)
        check('drag an AI step from the palette', counts(page)['nodes'] == 3)
        # fit so everything is in view
        page.click('button[aria-label="Fit to screen"]')
        page.wait_for_timeout(300)

        ids = page.evaluate("() => [...document.querySelectorAll('.react-flow__node')].map(n => [n.dataset.id, n.textContent])")
        intake = next(i for i, t in ids if 'Upload portal' in t)
        ocr = next(i for i, t in ids if 'Text recognition' in t)
        ext = next(i for i, t in ids if 'Extract fields' in t)

        e0 = counts(page)['edges']
        x1, y1 = center(page, f'.react-flow__node[data-id="{intake}"] .port.out')
        x2, y2 = center(page, f'.react-flow__node[data-id="{ocr}"] .nd')
        drag_mouse(page, x1, y1, x2, y2)
        check('connect two steps by dragging a port onto a step (snaps to its input)', counts(page)['edges'] == e0 + 1)
        x1, y1 = center(page, f'.react-flow__node[data-id="{ocr}"] .port.out')
        x2, y2 = center(page, f'.react-flow__node[data-id="{ext}"] .port.in')
        drag_mouse(page, x1, y1, x2, y2)
        check('connect output port to input port', counts(page)['edges'] == e0 + 2)

        # plug a model: drop the Language model component onto the AI step
        ex, ey = center(page, f'.react-flow__node[data-id="{ext}"] .nd')
        palette_drop(page, 'Language model', ex, ey)
        model_full = page.locator(f'.react-flow__node[data-id="{ext}"] .slot.full').count()
        check('plug a model into an AI step (drop onto the step)', model_full >= 1 and counts(page)['edges'] == e0 + 3)
        # output schema through the slot "+" menu
        page.locator(f'.react-flow__node[data-id="{ext}"] button[aria-label="Add Output"]').click()
        page.locator('.menu .mi', has_text='Output schema').click()
        page.wait_for_timeout(300)
        check('add an output schema from the slot menu', counts(page)['edges'] == e0 + 4)
        shot(page, 'builder_built')

        # delete an edge with the midpoint ×, then undo
        page.click('button[aria-label="Fit to screen"]')
        page.wait_for_timeout(300)
        before = counts(page)['edges']
        mid = page.evaluate(
            """() => {
                for (const hit of document.querySelectorAll('.w-hit')) {
                    if (!hit.closest('.react-flow__edge').querySelector('.wa')) continue;  // main-flow wire
                    const len = hit.getTotalLength(); const pt = hit.getPointAtLength(len / 2);
                    const m = hit.getScreenCTM(); const x = pt.x * m.a + m.e, y = pt.y * m.d + m.f;
                    if (document.elementFromPoint(x, y) === hit) return [x, y];
                }
                return null;
            }"""
        )
        check('an unobstructed wire midpoint exists', mid is not None)
        page.mouse.move(mid[0], mid[1])
        page.wait_for_timeout(250)
        page.locator('.edel').first.click()
        page.wait_for_timeout(300)
        check('delete an edge with the midpoint ×', counts(page)['edges'] == before - 1)
        page.locator('#cv .react-flow__pane').click(position={'x': 20, 'y': 400})
        page.keyboard.press('Control+z')
        page.wait_for_timeout(300)
        check('undo restores the edge (Ctrl+Z)', counts(page)['edges'] == before)
        page.keyboard.press('Control+Shift+z')
        page.wait_for_timeout(300)
        check('redo removes it again (Ctrl+Shift+Z)', counts(page)['edges'] == before - 1)
        page.keyboard.press('Control+z')
        page.wait_for_timeout(1200)
        check('draft autosaved', page.locator('#saved').inner_text() == 'Saved')
        shot(page, 'builder_after_undo')

        # 3 ── test run on the AP template to completion with extracted data
        page.goto(BASE + '#builder-pl_ap')
        page.wait_for_selector('.react-flow__node')
        page.wait_for_timeout(800)
        page.click('.chead button:has-text("Test run")')
        page.wait_for_selector('#runp .logrow:has-text("Finished in")', timeout=30000)
        check('test run finishes', True)
        shot(page, 'builder_run_log')
        page.click('#runp [role=tab]:has-text("Extracted data")')
        rows = page.locator('#runp .ftab tbody tr').count()
        check('extracted data shows 10 invoice fields', rows == 10, f'{rows} rows')
        check('PII field is masked after redaction', page.locator('#runp .ftab', has_text='masked').count() == 1)
        shot(page, 'builder_extracted_data')
        page.click('#runp [role=tab]:has-text("Document")')
        page.wait_for_timeout(500)
        check('document overlays drawn', page.locator('#runp .ov.fld').count() >= 8)
        shot(page, 'builder_document')

        # 4 ── create a connection; only the hint is ever shown
        page.goto(BASE + '#connections')
        page.click('button:has-text("New connection")')
        page.select_option('select[name=svc]', 'http_api')
        page.fill('input[name=n]', 'Smoke ERP sandbox')
        page.fill('input[name=s_base_url]', 'https://erp-sandbox.example.com/api')
        page.select_option('select[name=auth_type]', 'api_key')
        page.fill('input[name=a_key]', SECRET)
        shot(page, 'connection_form')
        page.click('[data-ok]')
        page.wait_for_selector('.drawer')
        page.wait_for_timeout(400)
        hint = page.locator('[data-testid=secret-hint]').inner_text()
        check('new connection shows only the secret hint', hint == '••••' + SECRET[-4:], hint)
        page.click('.drawer button:has-text("Test connection")')
        page.wait_for_timeout(700)
        shot(page, 'connection_drawer')
        page.click('.drawer button[aria-label=Close]')
        page.wait_for_timeout(300)
        html = page.content()
        check('full secret never rendered', SECRET not in html)
        check('row lists the hint', page.locator('tr', has_text='Smoke ERP sandbox').locator('td', has_text='••••' + SECRET[-4:]).count() == 1)
        # delete-in-use → 409 dialog
        page.locator('tr', has_text='AP mailbox').locator('button[aria-label^="Actions"]').click()
        page.locator('.menu .mi', has_text='Delete').click()
        page.click('[data-ok]')
        page.wait_for_timeout(500)
        check('deleting a connection in use shows its usages (409)', page.locator('.dialog', has_text='is in use').count() == 1)
        shot(page, 'connection_in_use')
        page.click('[data-x]')

        # 5 ── command palette
        page.keyboard.press('Control+k')
        page.wait_for_selector('.cmdk')
        page.keyboard.type('Workers')
        page.keyboard.press('Enter')
        page.wait_for_timeout(400)
        check('⌘K palette navigates', '#workers' in page.url)

        check('no console errors during the whole session', not errors, '; '.join(errors[:5]))
        browser.close()

    failed = [r for r in results if not r[1]]
    print(f'\n{len(results) - len(failed)}/{len(results)} checks passed')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
