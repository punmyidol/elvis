# Second Brain Benchmarks

Run: `bash tests/sb_bench/run.sh` from repo root.
Outputs land in `tests/sb_bench/out/`.

---

## B1 — Evidence fabrication guard

**Phase:** synthesize  
**Input:** topic = "Helmet detection project stalled", ctx has only unrelated rows (gitignore fix, requirements bump, daily note about plants/dinner).

**Check `out/b1_notes.json` → `items[0].body`:**

- [ ] Evidence section contains `(none from this week)` OR every bullet quotes text that literally appears in the 3 input rows
- [ ] No bullet mentions helmets, cameras, ESP32, or detection unless those words appear verbatim in the input rows
- [ ] Note does not exceed ~200 words
- [ ] All four sections present: Signal, Evidence, Historical context, Suggested next action

**Fail signal:** any evidence bullet invents content not in the input rows.

---

## B2 — Calendar noise rejection

**Phase:** pick  
**Input:** calendar has `DS Final Exam` and `Micky BD`; sources have only a button-color git commit and a recipe note — nothing about DS, exams, studying, or Micky.

**Check `out/b2_topics.json`:**

- [ ] Output is `[]` OR no topic mentions studying, finals, exam, Micky, or birthday
- [ ] If a topic is picked, it relates only to source activity (button styling, recipe)
- [ ] `source_signals` on any picked topic does not claim signals from sources that weren't active

**Fail signal:** any topic that infers "Pun should study for DS" or references Micky purely from the calendar title.

---

## B3 — Cross-source threading

**Phase:** pick  
**Input:** git has `wip: helmet_detector.py — add bounding box logic`; obsidian has `Helmet detection project — components` (ESP32-CAM gap, Raspberry Pi). Nothing else.

**Check `out/b3_topics.json`:**

- [ ] At least one topic picked related to helmet detection
- [ ] That topic's `source_signals` contains both `"obsidian"` and `"git"`
- [ ] `reason` references the cross-source nature (active code work + component gap) rather than just restating one source

**Fail signal:** topic missed entirely, or surfaced with only one source signal.
