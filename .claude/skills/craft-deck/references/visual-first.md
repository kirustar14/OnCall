# Visual-first — the artifact catalog

The test, restated because it's the whole skill: **remove every word from the slide and it
must still make its argument.** Text labels the artifact; it doesn't replace it.

A corollary that catches a lot of near-misses: *an icon next to text is not a visual.* The
graphic has to carry information independently. A lock icon beside the words "secure by
default" adds nothing. A diagram showing what's encrypted and what isn't adds everything.

And: **a concept about change, relationship, or comparison must be demonstrated, not
described.** Two text boxes side by side is not a comparison; a morph between two states is.

---

## The catalog

Sixteen artifact types. Pick the one that matches what the slide is *doing*, not the one
that's easiest. Working markup for each is in `assets/slide-patterns.html`.

### Orientation

**1 · Title.** The motif, the headline sentence, the credit line. Nothing else.
The motif should be a real element from the design system you're presenting, animated subtly.
Avoid a decorative shape you invented for the deck — it will read as filler.

**2 · Statement.** Huge type, centred, one sentence, no artifact. Use *sparingly* — this is
the one slide type that legitimately has no visual, and it earns that by being rare. Reserve
it for the reframe, the rule, and the closing line. Three or four in a deck, maximum.

**3 · Section divider.** A label and a statement, no bullets. Its job is a beat of silence.

### Showing what exists

**4 · Filmstrip.** N screens in a row, recreated as miniatures, revealed in sequence — then
some of them dim, grey out, or get struck through. Perfect for auditing an existing flow:
the audience *watches* four of six screens disappear and understands "one decision" without
being told. Animate the elimination; that's where the information is.

**5 · Device frame.** One screen, full size, in a browser or phone chrome. Use when a single
screen is the subject. Recreate it in HTML rather than screenshotting: it stays crisp, and you
can animate it, annotate it, and swap states.

**6 · Live prototype.** A device frame that plays a scripted interaction on slide entry, with
a replay control. This is usually the peak of the deck. Keep it under ~15 seconds, narrate
lightly over it rather than talking before it, and make sure the *choreography* proves your
claim — not just that the thing moves. See `references/motion.md`.

### Showing detail

**7 · Zoom detail.** One or two UI elements magnified 3–4×, with leader-line annotations
pointing at specific pixels. The move that makes people believe you actually built it.
Anchor annotations to real elements rather than guessing at percentages.

**8 · Anatomy.** A single artifact with its parts labelled around the outside. Use when the
*structure* of a component is the argument — a card with four fixed sections, a form with
three provenance states.

**9 · Instrument row.** N variants rendered as *real working UI*, side by side, each captioned.
This is the upgrade path for any slide you were about to build as a table. A five-row table
describing five confirmation patterns is text; five miniature interfaces showing them is a slide.

### Showing change

**10 · Before → after pair.** Two states, an arrow between them, ideally with the specific
deltas called out beneath. The most legible comparison form there is.

**11 · Wipe slider.** Two states stacked, a draggable divider. Only works if both layers share
a geometric skeleton — same header height, same grid, same footer — otherwise the halves don't
register and it reads as noise. Worth the effort when the change is subtle and distributed.

**12 · Object promotion.** An element physically flies from one place to another — a sentence
lifting out of a footnote and landing on a card, a control moving from step 4 to step 1.
When your argument is *"this was in the wrong place,"* this is the artifact. The motion is the claim.

**13 · Failure loop.** An animated wrong state, running on a loop: a tap counter climbing past
eleven, a spotlight drifting away from a toast nobody saw, a value silently diverging. Failures
are more persuasive shown than described, and a loop lets you talk over it for as long as you need.

### Showing structure

**14 · Matrix.** Rows × columns, built cell by cell, with the decisive row landing last. Good
for a taxonomy or a decision space. Keep it to 4×4 or smaller — beyond that it's a table and
belongs in a document.

**15 · Diagram.** Nodes and links, animated. Two sources of truth drifting apart; a flow map
showing what collapsed into what. Draw it in SVG or HTML so you can animate it and so it stays
sharp — never an exported image.

**16 · Motion spec.** The easing curve drawn as a graph with a dot traversing it, the token
values listed, and two loops running side by side with different curves so the difference is
*felt*. The single best way to prove motion craft rather than assert it.

### Closing

**Artifact grid.** For impact or takeaways: three miniature versions of the things you made,
each with one line. Objects, not bullets. Because the artifacts are what actually travelled.

---

## Recreating UI in HTML

Always prefer rebuilding the interface over pasting a screenshot.

- **It stays crisp** at any projection resolution and any zoom.
- **You can animate it** — swap states, spring things in, dim things out.
- **You can annotate it** by anchoring markers to real elements, so they stay put when the
  layout reflows.
- **You can magnify it** without artifacts, which makes the zoom-detail pattern possible.

Do it by lifting the real tokens — hex values, radii, spacing, type stack — from the product's
own source, not from memory. See `references/brand.md`. Rebuilding from a recollection of
"roughly what the app looks like" produces a generic look-alike that a reviewer who knows the
product will spot instantly.

Where a real asset exists (a logo, a photograph, a rendered chart), use the real asset. And
where you can't produce something honestly, **draw a labelled placeholder rather than a bad
imitation** — a placeholder is honest, a bad fake is a claim you can't back.

## Annotation craft

Two rules save most annotation slides:

**Anchor, don't guess.** Attach markers to real DOM elements and compute their position, so
they survive a font swap or a layout change. Percentage-positioned labels drift the moment
anything reflows.

**Never cover the thing you're pointing at.** If a callout overlaps the pixel it references,
switch to a numbered dot on the element and a legend beneath the figure. Numbered legends
read better than floating labels in almost every case, and they don't fight the composition.

## Filling the frame

If a slide has a large empty region, the artifact is too small or the slide is doing too little.
Enlarge, or merge it with its neighbour. Whitespace is a design element when it's shaped;
it's a mistake when it's leftover. As a rough gate: no more than ~30% of the frame should be
empty at any edge.
