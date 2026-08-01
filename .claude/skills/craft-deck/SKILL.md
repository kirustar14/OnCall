---
name: craft-deck
description: Build a visual-first presentation deck as a single self-contained HTML file — one working design artifact per slide, the narrative held in speaker notes, motion that encodes meaning, and a synced speaker view. Use this whenever someone needs a deck, slides, a presentation, a portfolio or design case study to present, a conference talk, a design review, a pitch, a readout, or a "walk me through this project" narrative — including when they only say "turn this into slides," "make a deck out of this," "I'm presenting this Thursday," or "I need to show this to leadership." Also use it when a deck already exists and they want it to look better, feel more crafted, be less text-heavy, or have real motion. Prefer this over generating a .pptx unless the person explicitly asks for a PowerPoint file they can edit in PowerPoint.
---

# Craft deck

## Why this exists

A deck about your work is not a neutral container for the work. It is **evidence about** the work.
Mike Markkula called this *impute*: people form an opinion of a thing from the signals around it.
Present good design in a sloppy deck and the audience quietly concludes you can't tell the difference.

So the standard here is higher than "clear." The deck itself has to be a portfolio piece.

Two rules do most of the work. Everything else in this skill is in service of them.

> **1. Remove every word from a slide. It must still make its argument.**
> Text is a label. The artifact is the content. If deleting the copy leaves an empty
> rectangle, you have written a document and projected it.

> **2. Motion is allowed only when it carries information the two static states don't.**
> A morph between states *is* the argument. A star wipe refers to nothing. If you can't
> name what the movement means, cut it.

## The shape of a slide

Every content slide is exactly this, and nothing else:

```
[label]                    ← 11px mono, uppercase, tracked. Where we are.
[action title]             ← one sentence stating the takeaway, not the topic
[one line of context]      ← optional. Only if the artifact needs a caption to be legible.
────────────────────────
[   THE ARTIFACT   ]       ← fills the rest. This is the slide.
```

The story — the reasoning, the setbacks, the "and then engineering said" — lives in
**speaker notes**, not on the screen. If the audience is reading, they aren't listening.

Action titles matter more than they look. Read only your titles, in order, top to bottom.
They must tell the whole argument by themselves. That's the **ghost deck test**, and it's the
cheapest way to find out your structure is broken before you've built anything.

## Procedure

Work in this order. Skipping to the HTML is the most common way this goes wrong, because
the tool's defaults hijack the thinking.

**1 · Write the ghost deck first.** Titles only, in a text file. One sentence each,
each stating a takeaway. Run the ghost deck test. Fix the outline here, where it's free.
If you're adapting an existing document, this is also where you delete 80% of it.

**2 · Assign an artifact to every title.** Next to each title, write what the audience
will *look at*. If you can't name a visual, the slide is a thought, not a slide — merge it
into its neighbour or move it to the notes. Consult `references/visual-first.md` for the
catalog of artifact types and when each one applies.

**3 · Declare the system before placing pixels.** Write it down at the top of the file
as a comment: type scale (four sizes, no more), one or two background colours, the accent,
the motif, the layout rhythm. Consistency comes from a declared system, not from restraint
in the moment. `references/brand.md` covers how to derive this from a real product's tokens
rather than inventing a generic one.

**4 · Start from `assets/template.html`.** It's a working deck shell: fixed 1920×1080 stage
with scaling, keyboard and click navigation, the build system, the notes drawer, and the
synced speaker window. Don't rebuild any of that. Replace the token block and the slides.

**5 · Build the artifacts.** Copy the closest pattern from `assets/slide-patterns.html` and
adapt it. These are real, working blocks — device frames, magnified detail views, animated
failure states, motion specs, before/after pairs. Recreate the actual UI in HTML/CSS rather
than pasting screenshots: it stays crisp at any projection size, and it lets you animate
and annotate it. `references/motion.md` has the choreography recipes and the tokens.

**6 · Write the speaker notes.** One entry per slide in the `NOTES` array. This is where
the talk lives. `references/speaker-notes.md` has the format and the beat markers.

**7 · Verify.** Run `scripts/verify_deck.py`. It steps every slide, screenshots each one,
catches console errors, and flags slides that have drifted back toward being text.
Then open the screenshots and actually look at them. The script checks structure; only
your eye checks composition.

## The things that most often go wrong

**The deck fills up with paragraphs again.** It happens on the slides you're least sure
about — uncertainty leaks out as prose. When you catch yourself writing a third sentence,
that's the signal you haven't found the artifact yet. Go find it.

**Every slide gets the same treatment.** A deck where all 20 slides are a header and a
centred image is as monotonous as one that's all bullets. Vary the artifact type. The
catalog in `references/visual-first.md` exists so you have somewhere to vary *to*.

**Motion gets added because it's fun.** It is fun. Apply the test: name the information
the movement carries. "It's a nice entrance" is not an answer. Entrances are allowed —
they orient the eye to what arrived — but a *choreography* has to mean something.

**The build takes over the room.** Auto-play the builds on slide entry with a stagger and
advance whole slides with the arrow keys. Click-by-click builds make the presenter a
projectionist. The template does this already; don't undo it.

**The system drifts.** Six type sizes, two greys that are almost the same, three shadow
values with no hierarchy. This reads as carelessness even when nobody can name why.
`references/anti-slop.md` is the pre-delivery gate for exactly this.

## Length, and knowing what to cut

Roughly **45 seconds per slide** with notes. A 20-minute talk is about 20–24 slides —
slide *count* is free, slide *density* is not. Prefer 24 clean slides to 14 crowded ones.

For a design portfolio review specifically, the format is usually 2 projects in 45 minutes
plus discussion, so one case study is ~20 minutes. Hiring managers are consistent about
what they want and what they don't: **process is table stakes, outcomes and decisions are
the differentiator.** One or two slides on process, maximum. Use "I," credit collaborators
explicitly, and include a slide on what you'd change. `references/narrative.md` has the
rest of that, including how to open, and the three-act structure that keeps a room awake.

## Reference files

Read these as you reach the step that needs them, not all at once.

| File | When |
|---|---|
| `references/narrative.md` | Step 1 — structure, action titles, the sparkline, portfolio-review specifics |
| `references/visual-first.md` | Step 2 — the artifact catalog: 16 types, when each applies |
| `references/brand.md` | Step 3 — deriving a real design system instead of inventing a generic one |
| `references/motion.md` | Step 5 — motion tokens, the information test, choreography recipes |
| `references/speaker-notes.md` | Step 6 — how to write notes a presenter can actually use |
| `references/anti-slop.md` | Step 7 — the pre-delivery gate |

## Assets

| File | What it is |
|---|---|
| `assets/template.html` | The working deck shell. Start here. Tokens, stage, nav, builds, notes, speaker view. |
| `assets/slide-patterns.html` | 16 copy-paste slide archetypes, each with its CSS and its choreography. |
| `scripts/verify_deck.py` | Steps the deck, screenshots every slide, reports errors and text-density drift. |

## Output

A single self-contained `.html` file. Inline all CSS and JS. Fonts load from a CDN with a
system fallback so the deck still works offline. Deliver the file directly — it opens in any
browser, presents full-screen with `F`, and needs nothing installed.
