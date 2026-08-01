# Speaker notes — where the story actually lives

Once the slides are visual, the notes carry the entire narrative. They stop being a safety net
and become the script. Write them properly.

## Format

A `NOTES` array, one HTML string per slide, indexed to match the slide order:

```js
const NOTES = [
`<p><span class="beat">Open cold.</span> "I want to tell you about an argument my team had.
The argument turned out to be the design problem."</p>
<p>Don't explain the product yet. Let the title sit.</p>`,

`<p>Three plans — $70, $100, $150.</p>
<p><strong>Buying one is not like buying anything else online.</strong> Once per household,
per several years. Gated on your street address.</p>
<p><span class="beat">Land on the last node.</span> "Undo here is a truck roll, not a return
label. Hold onto that — it's why the rest of this talk exists."</p>`,
];
```

Two conventions do most of the work:

- **`<span class="beat">…</span>`** — a stage direction, rendered in a distinct colour. Things
  like *Open cold.* / *Wait for the four to grey out.* / *Let it sit for a beat before speaking.*
  / *Say this plainly.* These are what you'll actually scan for under pressure.
- **`<strong>…</strong>`** — the sentence you want to land. One or two per slide. When you're
  nervous and skimming, these are what your eye catches.

## What belongs in the notes

Everything you removed from the slide, plus everything you'd say anyway:

- The reasoning behind a decision, and the alternative you rejected
- The scene — who objected, what they said, what changed your mind
- The numbers you'd cite if asked, so you don't have to remember them
- The anticipated question and your answer (`<span class="beat">If asked why no confirmation
  dialog here:</span> …`)
- Timing cues for anything animated: what to point at, when to stop talking

## Writing them so they're usable live

**Write in the voice you'll actually speak in.** Put your real sentences in quotes so you can
read them verbatim if you freeze. Formal written prose is unreadable at a podium.

**Front-load the stage direction.** The first thing on the slide's notes should be what to *do*,
not what to say. You'll glance for two seconds.

**Three to five short paragraphs, maximum.** If you need more, the slide is doing too much and
should be two slides.

**Say the uncomfortable thing yourself.** "Not yet launched." "This is the one I couldn't
resolve." Putting it in the notes makes sure you say it early rather than getting cornered later,
and saying it first buys you credibility.

**Narrate over animation, not before it.** For any slide with a scripted sequence, the note
should say when to start talking. Explaining what's about to happen and then playing it is
the most common way a demo goes flat.

## Two ways to read them

The template ships both, and they serve different moments:

**`N` — inline drawer.** A panel slides up over the bottom of the deck. Use this for rehearsal
and for reviewing the deck alone. Never use it live: the audience sees it.

**`S` — speaker window.** Opens a second browser window with the current slide's notes, a
running timer, and a preview of what's next, synced over `BroadcastChannel`. This is the one
you present with — put it on your laptop, the deck on the projector.

Practical notes on the speaker window: it's `window.open`, so the first press may be caught by
a popup blocker — allow it before you go on stage. The timer starts when the window opens, so
open it as you begin. If `BroadcastChannel` isn't available, the template falls back to polling,
which is slightly laggier but works.

## Rehearsal

Turn the notes on and read the deck end to end at speaking pace, out loud, with a timer.
Roughly 45 seconds per slide is the planning number, but your real number will differ and the
only way to know it is to say the words.

Mark the cut candidates while you rehearse — the aside you keep skipping, the second example
that makes the same point. Every deck runs long the first time. Knowing *in advance* which two
minutes you'll drop is the difference between finishing cleanly and being cut off.
