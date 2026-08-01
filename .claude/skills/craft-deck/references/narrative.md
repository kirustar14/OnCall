# Narrative — getting the structure right before you build

## The ghost deck test

Write only your slide titles, in order, in a plain text file. Read them top to bottom.
**They must tell the complete argument on their own.** If they don't, the deck is broken
and no amount of visual craft will fix it.

This is Barbara Minto's action-title rule, and it's the highest-leverage content rule there is.
A title states the *takeaway*, not the *topic*.

| Topic title (weak) | Action title (strong) |
|---|---|
| Research findings | Users never noticed the assistant |
| Design process | The argument wasn't about what the agent would say |
| Confirmation patterns | v1 confirmed everything — and taught people to stop reading |
| Results | Eight of the fixes need no AI at all |

A good test while drafting: if the title could sit on a slide in someone else's deck about
a different project, it isn't specific enough yet.

## The three relationships between consecutive slides

There are only three, and picking the right transition tells the audience which one they're in:

- **This is the same thing, transformed** → a morph. The audience keeps their eye on the
  thing that changed, and the change becomes the content.
- **This is the next thing in a sequence** → a directional move. Forward means forward.
- **This is unrelated** → a fade through. Honest signal that there's no continuity to preserve.

A deck that uses one transition for all three throws away free information. A deck that uses
six different transitions randomly is lying about the structure. The template ships with
fade-through as the slide-level default because most slide changes genuinely are topic changes;
use the morph patterns *within* a slide for the transformation cases.

## The spine: oscillation, not a list

Nancy Duarte's observation is that a talk that holds a room oscillates between **what is**
(the honest status quo) and **what could be** (the contrasting possibility). The gap between
them generates the tension that moves people. A flat presentation is one that stays in one state.

The two failure modes:
- Too long in *what is* → the audience is bored before you arrive
- Only *what could be* → it reads as unbelievable, disconnected from any present reality

Practically, that means: don't stack all your problems at the front and all your solutions at
the back. Alternate. Show a broken thing, show the fix, show the next broken thing.

## Three acts

The structure that keeps a room awake, adapted from Carmine Gallo's read of Apple keynotes:

**Act I — set up the stakes.** Answer *why should I care?* in the first two minutes.
Name the antagonist. It can be a competitor, but it's usually a problem or a status quo:
"the dial nobody could place," "a footnote two screens too late." A talk without a villain
has no tension.

**Act II — deliver the experience.** This is where the artifacts live. Somewhere in here,
put the **one thing they'll remember** — a demo, a single shocking number, a visual that
reframes everything. Plan it deliberately as a peak; don't hope one emerges. And change the
mode roughly every ten minutes: a live demo, a zoom-in, a failure loop, a different rhythm.
Attention decays and you have to reset it.

**Act III — land it.** End on a conclusion slide that can stay up during Q&A. Never end on
"Thank You" or a blank screen — you're giving away the most valuable real estate in the room.
An "and one more thing" beat before the close is a real structural device, not a gimmick: it
gives the ending a second peak.

## The single-sentence headline

Every launch Steve Jobs did had exactly one sentence describing the thing, under 140 characters,
repeated verbatim in the keynote, the press release and the website. "1,000 songs in your pocket."

Write yours before you build. It becomes your title slide, your closing slide, and the sentence
people repeat when they describe your project to someone else. If you can't write it, you don't
yet know what the project is.

## For a design portfolio review specifically

Hiring managers are strikingly consistent about this, and most portfolio decks ignore it.

**Format.** Usually two projects in ~45 minutes plus 15 minutes of discussion. One case study
is therefore ~20 minutes. Prepare a deck; don't walk through Figma files live.

**Process is table stakes.** One to two slides, total. Every source says the same thing and
Erik Kennedy puts it most bluntly: *if you can achieve the results, it's assumed you know the
process; if you can't, it doesn't matter how much process you know.* The differentiator is
decisions and outcomes.

**Show the solution early.** Counterintuitive but consistent advice: put a prototype or a
hi-fi view relatively near the front, to ground the audience, then spend the bulk of the time
on the decisions that got you there. Chronological order is the instinct and it's usually wrong.

**Say "I," and credit people by name.** Ambiguity about ownership is the number one killer.
"We redesigned the flow" tells a reviewer nothing about what you did. State your role, the team
composition, and the duration explicitly on an early slide.

**Show collaboration, don't assert it.** "I aligned stakeholders" is an adjective. "Our staff
engineer killed my favourite option with an objection I hadn't considered, and the constraint
produced a better answer than my original" is a scene. The second one is what seniority sounds
like. Include the artifacts of collaboration where you have them.

**Include what you got wrong.** A slide on the thing you'd change, or the residual you couldn't
design away, reads as senior — not as weakness. A version that is simply better than all its
predecessors reads as invented.

**Be explicit about what shipped.** If it's pre-launch, say so yourself, early, before anyone
asks. Then lean on the quality of the reasoning and name the metrics you'd measure. A reviewer
who discovers it mid-conversation re-reads every claim you made with suspicion.

**Each case study should contain:** the user problem, the business goal, research (qualitative
and quantitative), the iterations, the visual design, the impact, and the reflection. If a
metric exists, state the baseline too — a delta without a baseline is a number without meaning.

## Density limits worth holding

- One idea per slide. Corollary: 24 clean slides beat 14 crowded ones.
- Two to three short lines of text on a content slide, never a paragraph.
- At most two designs per slide — nobody should have to squint at your craft.
- ~45 seconds per slide with notes; use it to sanity-check your runtime.

## When slides are the wrong artifact

Tufte's critique is worth holding onto: for any decision that turns on dense evidence,
produce a written document and let people read it. Slides are for the parts that are
genuinely visual. If you find yourself building a slide that's really a table of numbers
someone needs to study, split the artifact — project the picture, hand out the document.
