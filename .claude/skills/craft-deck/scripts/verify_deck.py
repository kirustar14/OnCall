#!/usr/bin/env python3
"""
verify_deck.py — step a craft-deck HTML file, screenshot every slide, and report
the things that are cheap to check mechanically.

    python verify_deck.py deck.html [--out shots/] [--width 1600] [--settle 1200]

What it checks:
  · console errors and page exceptions, stepping forward AND backward
  · slide count, and that every slide is reachable
  · text density — flags slides drifting back toward being a document.
    Only the DECK's own copy is counted (header + deck type classes); text inside a
    recreated interface is part of the artifact, not prose, so it's excluded.
  · empty canvases — a header with no artifact is a thought, not a slide
  · font sizes below the legibility floor
  · presence of the nav affordances (N notes, S speaker view)

What it can't check: composition. Open the screenshots and look at them.

Requires: pip install playwright && playwright install chromium
"""
import argparse, json, os, sys, asyncio, re

WORD_LIMIT = 45          # on-screen words per slide before it reads as a document
MIN_FONT_PX = 20         # on a 1920x1080 stage; below this is a footnote
EMPTY_CANVAS_CHARS = 8   # a canvas with less than this much content is suspect


async def run(path, out, width, settle):
    from playwright.async_api import async_playwright
    height = round(width * 9 / 16)
    errors, report = [], {}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": width, "height": height},
                                      device_scale_factor=2)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        def _console(m):
            # network failures (offline webfonts) aren't code errors — the deck is
            # supposed to degrade gracefully, so don't fail the run on them
            if m.type == "error" and "net::ERR" not in m.text:
                errors.append(f"console: {m.text}")
        page.on("console", _console)

        await page.goto("file://" + os.path.abspath(path))
        await page.wait_for_timeout(1200)

        n = await page.evaluate("document.querySelectorAll('.s').length")
        if not n:
            print("✗ no slides found — is this a craft-deck file?"); return 1
        report["slides"] = n

        keys = await page.evaluate("""() => ({
            notes:   !!document.getElementById('notes'),
            speaker: /openSpeaker/.test(document.documentElement.innerHTML),
            dots:    !!document.getElementById('dots'),
        })""")
        report["affordances"] = keys

        os.makedirs(out, exist_ok=True)
        slides = []
        for i in range(n):
            await page.evaluate(f"document.querySelectorAll('#dots i')[{i}]?.click()")
            await page.wait_for_timeout(settle)
            shot = os.path.join(out, f"slide-{i+1:02d}.png")
            await page.screenshot(path=shot)

            info = await page.evaluate("""() => {
              const s = document.querySelector('.s.on'); if (!s) return null;
              const head = s.querySelector('.shead');
              const canvas = s.querySelector('.canvas');
              const txt = t => (t||'').replace(/\\s+/g,' ').trim();
              // the deck's own copy — NOT text inside a recreated interface
              const DECK_TEXT = '.shead, .ctx, .t-mega, .t-big, .t-mid, .t-sm, .t-xs, .zlegend';
              let words = 0, small = [];
              const nodes = [...s.querySelectorAll(DECK_TEXT)]
                .filter(el => !el.parentElement.closest(DECK_TEXT));   // dedupe nesting
              nodes.forEach(el => {
                words += txt(el.innerText).split(' ').filter(Boolean).length;
              });
              // legibility: leaf text only, and .lbl is a deliberate small-caps label
              nodes.forEach(root => root.querySelectorAll('*').forEach(el => {
                if (el.children.length || !txt(el.innerText) || el.closest('.lbl')) return;
                const px = parseFloat(getComputedStyle(el).fontSize);
                if (px && px < 20) small.push(px.toFixed(0) + 'px: ' + txt(el.innerText).slice(0, 40));
              }));
              return {
                title:  txt(head && head.querySelector('h2') ? head.querySelector('h2').innerText : ''),
                words,
                hasCanvas: !!canvas, hasHead: !!head,
                canvasChars: canvas ? canvas.children.length * 100 + txt(canvas.textContent).length : 0,
                small: small.slice(0, 4),
                statement: s.classList.contains('center'),
              };
            }""")
            info["index"] = i + 1
            info["shot"] = shot
            slides.append(info)

        # step forward then backward to catch choreography/cancellation bugs
        for _ in range(n + 1): await page.keyboard.press("ArrowRight"); await page.wait_for_timeout(160)
        for _ in range(n + 1): await page.keyboard.press("ArrowLeft");  await page.wait_for_timeout(110)
        await browser.close()

    report["errors"] = errors
    report["slides_detail"] = slides

    # ── findings ──
    wordy   = [s for s in slides if s["words"] > WORD_LIMIT]
    # only slides using the header+canvas grammar are held to it; custom layouts
    # (a full-bleed prototype, say) are legitimate and opt out by having no .shead
    empty   = [s for s in slides if s["hasHead"] and s["canvasChars"] < EMPTY_CANVAS_CHARS]
    nocanvas= [s for s in slides if s["hasHead"] and not s["hasCanvas"]]
    tiny    = [s for s in slides if s["small"]]
    statements = [s for s in slides if s["statement"]]

    print(f"\n  {n} slides · screenshots in {out}/\n")
    ok = True

    def bad(msg):
        nonlocal ok; ok = False; print(msg)

    if errors:
        bad(f"  ✗ {len(errors)} console error(s):")
        for e in errors[:6]: print("      " + e[:120])
    else:
        print("  ✓ no console errors, forward and backward")

    if not (keys["notes"] and keys["speaker"] and keys["dots"]):
        bad(f"  ✗ missing affordances: {keys}")
    else:
        print("  ✓ notes drawer, speaker view and dots present")

    if wordy:
        bad(f"  ✗ {len(wordy)} slide(s) over {WORD_LIMIT} words — drifting back to a document:")
        for s in wordy: print(f"      {s['index']:>2}. {s['words']} words — {s['title'][:60]}")
    else:
        print(f"  ✓ every slide under {WORD_LIMIT} on-screen words")

    if nocanvas or empty:
        bad("  ✗ slide(s) with a header but no artifact:")
        for s in nocanvas + empty: print(f"      {s['index']:>2}. {s['title'][:60]}")
    else:
        print("  ✓ every content slide has an artifact")

    if len(statements) > 4:
        print(f"  ⚠ {len(statements)} statement slides — these earn their keep by being rare (≤4)")

    if tiny:
        print(f"  ⚠ text below {MIN_FONT_PX}px on {len(tiny)} slide(s):")
        for s in tiny[:4]: print(f"      {s['index']:>2}. {', '.join(s['small'])}")

    print("\n  Now open the screenshots. This script checks structure;")
    print("  only your eye checks composition.\n")

    with open(os.path.join(out, "report.json"), "w") as f:
        json.dump(report, f, indent=2)
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("deck")
    ap.add_argument("--out", default="deck-shots")
    ap.add_argument("--width", type=int, default=1600)
    ap.add_argument("--settle", type=int, default=1200,
                    help="ms to wait per slide so choreography completes")
    a = ap.parse_args()
    sys.exit(asyncio.run(run(a.deck, a.out, a.width, a.settle)))
