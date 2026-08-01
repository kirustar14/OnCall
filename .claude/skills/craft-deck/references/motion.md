# Motion — the tokens and the test

## The test

> **Does the motion carry information the two static states don't?**
> Yes → it's a build. No → it's a wipe. Cut it.

Cheesy isn't a style judgement; it's what happens when motion has no referent. Star wipes,
spins, cube transitions and letter-by-letter reveals all fail because nothing in the content
corresponds to the movement. A 400ms morph of a diagram from state A to state B isn't cheesy
at any level of flourish, because the movement *is* the argument.

Mike Bostock's mechanism for why the good case works is **object constancy**: when a bar
re-sorts, a viewer who can track that bar through the motion never has to re-read its label.
Identity is carried by continuity rather than by re-reading. Give every element a stable key
across states and the transition does cognitive work for free.

The corollary is a warning: transitions *between unrelated things* are meaningless, because
there is no identity to preserve. That's why slide-level transitions should mostly be a plain
fade — most slide changes really are topic changes — and the interesting motion should live
*inside* a slide, where things genuinely persist.

## Principles from the research

From Heer & Robertson's work on animated transitions, all of it directly applicable:

- **Intermediate frames must remain true.** The middle of a transition must not be a lie.
- **Group things that change together.** The eye tracks a group as one object instead of N.
- **Minimise occlusion.** Objects that pass over each other become untrackable.
- **Prefer simple motions.** Translation and expand/contract are easier to read than rotation.
- **Stage complex changes.** Break one complicated transformation into a sequence of simple ones.
  This is the research justification for progressive builds: don't reveal a complex diagram at
  once, and don't transform it in one step either.
- **~1 second is right for a data transition** — notably slower than UI animation guidance,
  because the task is comprehension, not responsiveness. Deck motion can be slower than app
  motion; nobody is waiting on it.

## Tokens

These are Material 3's published easing and duration tokens. They're a good default even
outside Google-flavoured work, because they're tuned and internally consistent — but if the
product you're presenting has its own motion tokens, use those instead and say so on the slide.

```css
--e-emph:        cubic-bezier(.2,0,0,1);      /* standard / emphasized */
--e-emph-dec:    cubic-bezier(.05,.7,.1,1);   /* hero entrances */
--e-emph-acc:    cubic-bezier(.3,0,.8,.15);   /* hero exits */
--e-std-dec:     cubic-bezier(0,0,0,1);       /* entering elements */
--e-std-acc:     cubic-bezier(.3,0,1,1);      /* exiting elements */
/* linear is for indeterminate progress only — never for anything the eye tracks */

/* durations */
short  50 / 100 / 150 / 200ms
medium 250 / 300 / 350 / 400ms
long   450 / 500 / 550 / 600ms
x-long 700 / 800 / 900 / 1000ms
```

**Spring tokens** (Material 3 Expressive), given as damping ratio ζ and stiffness k, with
usable CSS approximations:

| Role | ζ / k | CSS | Overshoot |
|---|---|---|---|
| spatial · fast | 0.6 / 800 | `350ms cubic-bezier(.42,1.67,.21,.90)` | ~9.5% |
| spatial · default | 0.8 / 380 | `500ms cubic-bezier(.38,1.21,.22,1.00)` | ~1.5% |
| spatial · slow | 0.8 / 200 | `650ms cubic-bezier(.39,1.29,.35,.98)` | ~1.5% |
| effects · all | 1.0 | plain easing, no bounce | none |

The distinction is load-bearing and worth putting on a slide if motion is part of your story:
**spatial** = anything that moves, resizes or reshapes, and it may overshoot. **effects** =
colour and opacity, ζ = 1.0, never overshoots. Nothing that means *committed* should bounce.

## Choreography recipes

**Slide entry.** Fade the outgoing slide on an accelerating curve, fade the incoming one on a
decelerating curve with a short delay. Then stagger the slide's builds ~90ms apart.
Auto-play them — see the note on presenter control below.

**Element entrance.** `opacity 0→1` over 400ms standard-decelerate, plus `translateY(26px→0)`
over 500ms emphasized-decelerate. For something that should feel like it *arrived*, swap the
transform curve for spatial-default so it overshoots slightly.

**Sequential reveal (timeline, filmstrip).** Step through with ~1s between items and let a
progress line fill underneath on a linear curve — linear is correct here because it's
indeterminate progress, not something the eye tracks to a destination.

**Elimination (filmstrip dim-out).** Stagger ~280ms apart so the audience registers each one
individually. Simultaneous elimination reads as a single event and loses the count.

**Object promotion (element flies A → B).** Measure both anchors with `getBoundingClientRect`,
account for the stage's scale factor, position a clone at the source, then transition its
position over ~1.1s on the emphasized curve. Fade the source to ~18% as it leaves and fade the
destination in as it lands. Slower than app motion on purpose — the audience needs to follow it.

**Failure loop.** Run on an interval, but gate it on the slide being visible so it isn't
burning cycles or desynchronising in the background:

```js
setInterval(() => {
  if (!el.closest('.s').classList.contains('on')) return;
  /* … one beat of the loop … */
}, 900);
```

**Streaming text.** Split into word spans, then reveal each with `opacity 0→1` and a
`blur(3px)→0` over ~260ms standard-decelerate, staggered ~18ms. The de-blur is what makes it
read as *generated* rather than typed. A shimmer band or block cursor trails it.

**Shimmer / working state.** A narrow bright band swept across on a diagonal:

```css
background: linear-gradient(-65deg, transparent 40%, rgba(255,255,255,.9) 50%, transparent 60%);
background-size: 400% 400%;
animation: sweep 2s linear infinite;
@keyframes sweep { 0% {background-position:100% 100%} 70%,100% {background-position:0 0} }
```

The 70% keyframe hold matters: the sweep completes in 1.4s and rests for 0.6s. A continuous
sweep reads as frantic.

## Presenter control

Auto-play builds on slide entry; advance whole slides with the arrow keys. Click-by-click
builds turn the presenter into a projectionist and make it very easy to lose your place.
The exception is a genuine reveal you want to time to a sentence — do that with a deliberate
pause in the choreography, not by handing the click to the presenter.

## Accessibility

Always honour reduced motion. Vestibular disorders affect a large share of adults, and a
projected deck is exactly the situation that triggers them.

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: .001ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: .001ms !important;
  }
}
```

For scripted choreography, clamp the waits too — the template's `wait()` helper does this,
so timed sequences still complete rather than hanging.

## Cancellation

Any multi-step choreography needs a token so a fast presenter doesn't leave two sequences
running at once:

```js
let tok = 0;
function run() {
  const id = ++tok;
  /* … */ await wait(400); if (id !== tok) return; /* … */
}
```

Check the token after every await. Without this, arrowing back and forth produces overlapping
animations that look broken and are very hard to debug.
