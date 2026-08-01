# The pre-delivery gate

Run this before you hand the deck over. Each item is a thing that reads as carelessness even
when the viewer can't name why.

## Structure

- [ ] **Ghost deck test passes.** Read only the titles, in order. They tell the whole argument.
- [ ] **Every content slide has an artifact.** Delete the text from any slide at random — it
      still makes its point. Statement slides are the exception and there are ≤4 of them.
- [ ] **No slide has more than ~40 words** of on-screen copy.
- [ ] **The artifact types vary.** Not twenty slides of header-plus-centred-image.
- [ ] **The last slide is a conclusion**, not "Thank You" or a blank. It stays up during Q&A.
- [ ] **The narrative is in the notes**, and the notes are written in speaking voice with beat
      markers — not a copy of the slide text.

## Type

- [ ] **One family.** Hierarchy comes from size, weight and width.
- [ ] **Four sizes maximum**, plus one mono label style.
- [ ] **The display face isn't a browser default** (Inter, Roboto, Arial, system-ui) — unless
      that's genuinely what the brand uses.
- [ ] **Body text clears the floor** for the viewing context: ~3% of frame height for a
      screen-shared deck, ~6.5% for a projected keynote.
- [ ] **Font fallbacks include a weight**, not just `font-variation-settings` — variable-font
      settings silently do nothing if the webfont fails to load, and decks get presented offline.
- [ ] **Line length under ~60 characters** anywhere there's more than one line.

## Colour and depth

- [ ] **All colours are CSS variables.** No `#4f46e5` in one place and `#5046e6` in another.
- [ ] **One colour dominates** (60–70% of weight); one sharp accent; nothing has equal weight.
- [ ] **The palette is specific to this subject.** Drop it into an unrelated deck — if it still
      works, it isn't specific enough.
- [ ] **Body text passes 4.5:1**, and anything projected is closer to 7:1.
- [ ] **No purple→blue gradient on white.** `linear-gradient(135deg,#667eea,#764ba2)` and its
      relatives are the single most recognisable AI-slop fingerprint.
- [ ] **Shadows have a hierarchy** — small, medium, large. Identical shadows everywhere flatten
      the whole composition.
- [ ] **The background has some depth** — a gradient field, a grain, a subtle bloom. A flat fill
      bands badly on projectors and reads as unfinished.
- [ ] **No meaning encoded in hue alone.**

## Layout

- [ ] **Fixed 16:9 stage, scaled to fit.** Never responsive reflow — you cannot rehearse a
      layout that changes shape on the projector.
- [ ] **One grid, held across the deck.** Break it deliberately once or twice, for the peak
      moment or a full-bleed — a break only reads as emphasis if the grid is intact either side.
- [ ] **No more than ~30% of the frame is empty at any edge.** If content doesn't fill, enlarge it.
- [ ] **Recreated UI uses the product's real tokens**, in a separate variable group from the
      deck's own chrome colours.

## Motion

- [ ] **Every animation names its information.** If you can't say what the movement means, it's
      a wipe. Cut it.
- [ ] **Linear easing appears only on indeterminate progress.** Never on anything the eye tracks.
- [ ] **Builds auto-play on slide entry**, staggered. The presenter advances slides, not builds.
- [ ] **Long choreographies have a cancellation token**, checked after every await.
- [ ] **Looping animations are gated on slide visibility** so they don't run in the background.
- [ ] **`prefers-reduced-motion` is honoured**, including in scripted sequences.
- [ ] **Intermediate frames are never a lie.**

## Mechanics

- [ ] **Self-contained single HTML file.** Inline CSS and JS; data URLs for images.
- [ ] **Webfonts degrade gracefully.** Present it once with the network off and confirm.
- [ ] **No console errors** — step every slide forward and backward.
- [ ] **Keyboard nav works**: `← →`, `Home`, `End`, `F` fullscreen, `N` notes, `S` speaker view.
- [ ] **Click zones work**: left ~28% goes back, the rest goes forward.
- [ ] **The speaker window opens and syncs.** Test it before you need it — popup blockers.
- [ ] **You have looked at every slide as an image.** Run `scripts/verify_deck.py` and open the
      screenshots. The script checks structure; only your eye checks composition.

## Honesty

- [ ] **Placeholders are labelled as placeholders.** A placeholder is honest; a bad imitation of
      the real thing is a claim you can't back.
- [ ] **No invented content to fill space** — no dummy sections, no decorative statistics. If a
      section feels empty, solve it with composition, not fabrication.
- [ ] **Substitutions are footnoted.** Font stand-ins, reconstructed screens, estimated numbers.
- [ ] **Shipped vs. proposed is stated explicitly**, by you, early.
- [ ] **Every number has a source**, and every metric has a baseline.
