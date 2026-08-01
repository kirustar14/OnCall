# Brand — deriving a real system instead of inventing a generic one

## The failure mode

Grabbing "a font and two colours" from a brand and calling it done is the most reliable way
to produce generic tech design. Every brand ends up looking the same because the parts that
actually differentiate — the shape language, the spacing rhythm, the motion character, the
one weird glyph — get left behind.

The rule: **logos, product screenshots and real UI are first-class citizens. Colours and fonts
are auxiliary.** If you're presenting work on a real product, the product's own interface is
the strongest asset you have. Rebuild it faithfully.

## Where the real tokens are

Before inventing anything, spend twenty minutes looking. Design systems leak, and the leaked
version is more accurate than your memory.

1. **The product's own CSS.** Many apps ship their design-token layer as CSS custom properties
   right in the served HTML. Fetch the page and grep for custom properties:
   ```bash
   curl -A "Mozilla/5.0 Chrome/126" https://example.com/ | grep -oE '\-\-[a-z-]+sys[^;{}"]+'
   ```
   This is the single highest-yield move and it's frequently overlooked.
2. **Public design-system docs** — Material, Carbon, Polaris, Lightning and their peers publish
   full token sets, including motion.
3. **Open-source component repos.** Token files (`*Tokens.kt`, `_tokens.scss`, `theme.ts`) are
   the authoritative values, not the marketing site.
4. **Brand asset SVGs.** A logo SVG contains the exact gradient stops and angles. Read the file.
5. **Font availability.** Check what's actually licensed for web use — several corporate faces
   have quietly been open-sourced, and several haven't. Substituting when you didn't have to is
   a needless loss of fidelity.

Whatever you can't verify, say so in a footnote on the last slide. "Google Sans isn't publicly
distributed; DM Sans stands in" is a mark of rigour, not an apology.

## The token block

Declare everything at the top of the file. The template's block is organised like this — keep
the structure and swap the values:

```css
:root{
  /* — brand: the product you're presenting — */
  --g1:#4285f4; --g2:#9b72cb; --g3:#d96570;   /* the signature gradient */
  --accent:#3186ff;                            /* the one accent */

  /* — stage: the deck's own surface, not the product's — */
  --ink:#f4f2f7; --ink-2:rgba(244,242,247,.62); --ink-3:rgba(244,242,247,.38);
  --bg:#070609; --hair:rgba(244,242,247,.10);

  /* — product surface: for recreated UI, lifted verbatim — */
  --p-surface:#fff; --p-container:#f0f4f9; --p-on:#1f1f1f; --p-outline:#c4c7c5;

  /* — motion — */
  --e-emph:cubic-bezier(.2,0,0,1); --spring-def:cubic-bezier(.38,1.21,.22,1);
}
```

Keeping *stage* and *product* colours in separate groups matters more than it looks. The deck's
chrome and the interface you're showing are two different systems, and mixing them is why
recreated UI so often looks subtly wrong — grey text from the deck's palette leaking into a
component that should use the product's `on-surface`.

## Choosing a stage palette

**One colour should dominate — 60–70% of the visual weight — with one or two supporting tones
and one sharp accent.** Never give colours equal weight; that's what makes a deck look like a
template.

A useful specificity test: **if you could drop your palette into a completely different
presentation and it would still "work," you haven't made specific enough choices.** The palette
should feel derived from this subject.

Dark throughout reads as premium and projects well in a dim room. Light throughout reads as
editorial and photographs better. Dark title and conclusion with light content in between is
the classic sandwich. Pick one mode and commit — mixing a dark editorial deck with light
consulting slides is the most common way a deck loses coherence.

Contrast: WCAG AA is 4.5:1 for body text; push toward 7:1 for anything projected, because
projector gamma and ambient light eat contrast that looked fine on a laptop. Saturated accents
(neon purple, hot pink) routinely fail AA on dark backgrounds — use a lighter tint for text and
reserve the saturated value for fills and borders. And never encode meaning in hue alone.

## Typography

**One family.** Use size, weight and width for hierarchy — never a second typeface. If the
family is variable, its axes are your hierarchy: hairline weights at display sizes read as
refined in a way a second font never will.

**Four sizes, maximum.** The template ships with mega / big / mid / small plus a mono label
style. Adding a fifth is almost always a sign that a slide is doing too much.

Avoid Inter, Roboto, Arial and system fonts *as display faces* — they're the browser defaults
and they signal no typographic decision was made. They're fine as fallbacks, and fine as body
text if they're what the brand actually uses. The distinction is whether the choice is
deliberate.

Size floors, as a percentage of frame height so they're resolution-independent:
- Projected keynote: body ~6.5% of height (28pt+ equivalent), headlines much larger
- Screen-shared or read-later: body ~3%, which is ~34px on a 1080-tall stage
- Below ~24px on a 1920×1080 stage, treat it as a footnote and expect nobody to read it

## Shape and spacing

Lift the product's radii and spacing scale if it has one; otherwise pick a scale and hold it.
Snap to an 8px grid. Give shadows a hierarchy — small, medium, large — because identical
shadows everywhere flatten the design and read as carelessness even when nobody can name why.

## The motif

Pick **one** distinctive element and repeat it: a glyph, a shape, a specific corner treatment.
It should come from the subject, not be invented for the deck.

Two things that don't count as a motif: an accent stripe under headings, and a coloured bar
in the corner. They're the default decisions, which is exactly why they read as nothing.

The strongest motifs do double duty — they're simultaneously the deck's signature *and* a real
element of the thing you're presenting, so every repetition reinforces the subject rather than
decorating around it.

## Design directions when the brief is vague

If nobody has told you what it should feel like, don't guess once — offer three directions from
genuinely *different schools*, not three variants of minimalism. Give each a one-line pitch, a
reference point, and three vibe words. People choose much better by seeing than by describing,
and it takes fifteen minutes to sketch three title slides.
