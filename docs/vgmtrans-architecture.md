# vgmtrans Architecture: the seq → MIDI conversion spine

> **Purpose & status.** This document maps how [vgmtrans](https://github.com/vgmtrans/vgmtrans)
> turns a raw game file into a Standard MIDI File, for the slice of the codebase GME cares
> about as an *oracle/spec*. It is written against the fork at `D:/git/vgmtrans/src/main/`
> and lives GME-side on purpose, to keep the fork clean for upstream PRs.
>
> **Scope (bounded).** Only the **sequence → MIDI** spine is documented:
> `io/RawFile → Scanner/Matcher → components/seq → VGMColl → conversion/MidiFile`.
> Sample-synthesis output (SF2/DLS), the Qt UI, and DSP/sample decoding are **siblings**
> off the same data model and are explicitly **out of scope** here — they are mentioned only
> where they touch the spine.
>
> **Accuracy convention.** `file:Symbol` anchors are the load-bearing part of this doc. Where
> a symbol or behavior was *not* directly confirmed in the source it is tagged **(unverified)**.
> Prefer fixing a wrong anchor over leaving it — a wrong pointer poisons the oracle.

---

## Part 1 — Narrative (big-picture orientation)

### 1.1 The pipeline

```
                       ┌──────────────────────────────────────────────────────────┐
                       │  io/RawFile  (DiskFile | VirtFile)                         │
                       │  mmap'd / in-memory bytes + typed readers (readByte/Short/ │
                       │  Word, LE & BE). Knows nothing about music.                │
                       └───────────────┬──────────────────────────────────────────┘
                                       │  raw bytes
                                       ▼
                       ┌──────────────────────────────────────────────────────────┐
                       │  components/Scanner  (VGMScanner::scan)                    │
                       │  Per-format byte-pattern search. On a hit, constructs      │
                       │  VGMFile subobjects: VGMSeq, VGMInstrSet, VGMSampColl.     │
                       └───────────────┬──────────────────────────────────────────┘
                                       │  VGMSeq / VGMInstrSet / VGMSampColl (each ::load()'d)
                                       ▼
                       ┌──────────────────────────────────────────────────────────┐
                       │  components/matcher/  (Matcher → AkaoMatcher)              │
                       │  Groups the loose VGMFiles that belong together into a     │
                       │  VGMColl (seq + instrset + 0..n sampcolls), keyed by id.   │
                       └───────────────┬──────────────────────────────────────────┘
                                       │  VGMColl  (bundle)
                                       ▼
   ┌─────────────────────────── components/seq/  (the sequence model / "IR") ───────────────────────────┐
   │  VGMSeq      owns SeqTrack[]   ── parseHeader / parseTrackPointers / loadTracks                     │
   │  SeqTrack    owns SeqEvent[]   ── readEvent() walks raw opcodes, calls addNoteByDur/addVol/...      │
   │  SeqEvent    typed event nodes ── NoteOnSeqEvent, VolSeqEvent, TempoSeqEvent, JumpSeqEvent, ...     │
   │                                                                                                     │
   │  The SAME parser (readEvent + the add* helpers) runs in three ReadModes:                            │
   │    READMODE_ADD_TO_UI        → builds the SeqEvent tree (the display/source model)                  │
   │    READMODE_FIND_DELTA_LENGTH→ measures each track's length in ticks (totalTicks)                   │
   │    READMODE_CONVERT_TO_MIDI  → emits MidiEvents into a MidiFile (see below)                         │
   └───────────────┬─────────────────────────────────────────────────────────────────────────────────┘
                   │  VGMSeq::convertToMidi(coll)
                   ▼
   ┌─────────────────────────── conversion/MidiFile  (one renderer) ──────────────────────────────────┐
   │  MidiFile owns MidiTrack[] owns MidiEvent[]                                                        │
   │  NoteEvent / ControllerEvent / ProgChangeEvent / PitchBendEvent / TempoEvent / ...                │
   │  MidiFile::saveMidiFile() serializes to a Standard MIDI File (SMF format 1).                       │
   └───────────────────────────────────────────────────────────────────────────────────────────────────┘

   (siblings off VGMColl, OUT OF SCOPE here: conversion/SF2Conversion, conversion/DLSConversion,
    which consume the VGMInstrSet + VGMSampColl branch instead of the seq branch.)
```

### 1.2 Data flow in prose

A `RawFile` (`io/RawFile.h` — concrete `DiskFile` is mmap-backed, `VirtFile` is an in-memory
slice, e.g. a PSF after decompression) is just bytes plus endian-aware accessors. It has no
musical knowledge.

A **format's `VGMScanner`** (`components/Scanner.h`, abstract `VGMScanner::scan`) sweeps the
RawFile for the byte signatures of that format. When it recognizes a sequence, instrument set,
or sample collection, it instantiates the corresponding `VGMFile` subclass (`VGMSeq`,
`VGMInstrSet`, `VGMSampColl`) and calls its `load()`. For AKAO these are `AkaoSeq`,
`AkaoInstrSet`, `AkaoSampColl` under `formats/Akao/`.

Those loose files are then handed to a **`Matcher`** (`components/matcher/Matcher.h`), whose job
is purely associative: figure out which seq goes with which instrument set and sample data, and
bundle them into a **`VGMColl`** (`components/VGMColl.h`). `AkaoMatcher`
(`formats/Akao/AkaoMatcher.cpp`) keys everything by id and assembles a collection in
`AkaoMatcher::tryCreateCollection`.

The **sequence model** lives in `components/seq/`. This is vgmtrans's analogue of an
intermediate representation: `VGMSeq` (`VGMSeq.h`) owns an ordered list of `SeqTrack`
(`SeqTrack.h`), each of which owns typed `SeqEvent` nodes (`SeqEvent.h`). The actual opcode
decoding lives in a format-specific `SeqTrack::readEvent()` override, which calls a large family
of `add*` helpers on the base `SeqTrack` (`addNoteByDur`, `addVol`, `addPan`, `addTempoBPM`,
`addProgramChange`, `addLoopForever`, `addJump`, …).

Finally, **`conversion/MidiFile`** (`conversion/MidiFile.h`) is one *renderer* off that model.
`VGMSeq::convertToMidi(coll)` produces a `MidiFile`, a tree of `MidiTrack` → `MidiEvent`, which
`MidiFile::saveMidiFile()` serializes to a Standard MIDI File.

### 1.3 The source-model / target-format split (and how it differs from GME)

The important architectural idea — and the one most relevant to GME's own design — is the
**split between an abstract sequence model and concrete output renderers**:

- `components/seq/` (`VGMSeq`/`SeqTrack`/`SeqEvent`) is the **source model**: a parsed,
  format-neutral representation of "what the music driver does."
- `conversion/MidiFile` is **one renderer** off that model. SF2 (`conversion/SF2Conversion`)
  and DLS (`conversion/DLSConversion`) are **sibling renderers** off the *instrument/sample*
  branch of the same `VGMColl`. So MIDI is not privileged; it is one of several exports.

This is directly analogous to GME's own two-pass design in `akao/`: GME parses raw AKAO opcodes
into IR events (`akao/ir_events.py`, Pass 1) and then renders that IR into MIDI / MusicXML / text
(`akao/output_generators.py`, Pass 2). vgmtrans's `SeqEvent` ≈ GME's IR event; vgmtrans's
`MidiFile` renderer ≈ GME's MIDI generator.

**The one subtlety to internalize** (this is where the analogy bends): in GME the IR is the
*pivot* — Pass 2 reads the IR list and renders from it. In vgmtrans the `SeqEvent` tree is
primarily a **display/inspection model**, and MIDI is **not** generated by walking the
`SeqEvent` list. Instead, `VGMSeq` **re-runs the same parser** in a different `ReadMode`
(`components/seq/ReadMode.h`). Concretely (see `VGMSeq::convertToMidi`, `VGMSeq.cpp:76`):

1. `loadTracks(READMODE_FIND_DELTA_LENGTH)` — walk every track to learn its length in ticks.
2. `loadTracks(READMODE_CONVERT_TO_MIDI, stopTime)` — walk again, this time the `add*` helpers
   emit `MidiEvent`s into a fresh `MidiFile`.

The `add*` helpers do **double duty** by branching on `readMode`. For example
`SeqTrack::addNoteByDur` (`SeqTrack.cpp:712`):

- in `READMODE_ADD_TO_UI` it calls `recordSeqEvent<DurNoteSeqEvent>(...)`, creating the IR node;
- it then calls `addNoteByDurNoItem(...)` (`SeqTrack.cpp:732`), which — *only* under
  `READMODE_CONVERT_TO_MIDI` — emits the real note via `pMidiTrack->addNoteByDur(channel, key +
  cKeyCorrection + transpose, finalVel, dur)`.

So the SeqEvent IR and the MIDI emission are **two interpretations of one parser walk**, selected
by mode, rather than IR-then-render. A `SeqEventTimeIndex` (`VGMSeq::timedEventIndex()`,
`m_timedEvents`) is built during the convert pass to associate the persisted SeqEvents with their
realized tick times (used by the UI to highlight events during playback) — but it is an *index
over* the model, not the thing MIDI is rendered from.

> **Oracle implication for GME.** When GME uses vgmtrans as a parity oracle, the authoritative
> "what opcode X does" lives in the format's `SeqTrack::readEvent()` override and which `add*`
> helper it calls — *not* in the SeqEvent class definitions, which only describe the display node.
> The MIDI bytes ultimately come from the `MidiTrack::add*` methods in `conversion/MidiFile.cpp`.

### 1.4 How a SeqEvent becomes a MIDI event (conceptual)

Walking the chain for a single musical event:

```
 raw opcode bytes
   → SeqTrack::readEvent()                  (format-specific opcode decode)
       → SeqTrack::addVol(offset,len,vol)   (base helper; branches on readMode)
           ├─ READMODE_ADD_TO_UI:  recordSeqEvent<VolSeqEvent>(...)  → SeqEvent node (display IR)
           └─ READMODE_CONVERT_TO_MIDI: addVolNoItem(vol)
                  → MidiTrack::insertVol/addVol(channel, vol, absTime)
                      → MidiTrack::addEvent<VolumeEvent>(...)   (ControllerEvent, CC#7)
                          → MidiEvent::writeEvent(buf, time)    (emit status+data bytes at save)
```

**Timing / tracks.** Tracks map roughly 1:1 — each `SeqTrack` gets a `MidiTrack`
(`SeqTrack::pMidiTrack`). The conversion runs against an absolute tick clock: `VGMSeq::time`
advances as events are parsed, and `MidiTrack::setDelta(time)` keeps each MIDI track's write
cursor aligned. MIDI events store absolute tick times (`MidiEvent::absTime`); deltas are computed
at serialization time. `MidiFile::globalTrack` holds events (e.g. tempo, time signature) that get
merged into every track on write. Events carry a `priority` (`PRIORITY_*` in `MidiFile.h`) so
that simultaneous events (bank-select before program-change, resets first, etc.) sort into the
correct order via `MidiEvent::operator<` / `MidiTrack::sort()`.

**Channel.** `SeqTrack::channel` / `channelGroup` set the MIDI channel; `channelGroup` lets a
sequence exceed 16 channels by routing groups to multiple MIDI "ports" (`MidiPortEvent`).

---

## Part 2 — Anchor layer (precise grounding for agents)

### 2.1 Key classes → `file:Symbol` → responsibility

All paths are under `D:/git/vgmtrans/src/main/`.

| Class / symbol | Anchor | Responsibility |
|---|---|---|
| `RawFile` (abstract) | `io/RawFile.h:RawFile` | Byte container + endian-aware readers (`readByte/readShort/readWord`, `get<T>`/`getBE<T>`). Holds `containedVGMFiles`. |
| `DiskFile` | `io/RawFile.h:DiskFile` | `RawFile` backed by an mmap (`mio::mmap_source`) of a file on disk. |
| `VirtFile` | `io/RawFile.h:VirtFile` | `RawFile` backed by an in-memory `std::vector<char>` (sub-slices, decompressed PSF, etc.). |
| `VGMScanner` (abstract) | `components/Scanner.h:VGMScanner` | Per-format scan entry point; `scan(RawFile*, void* offset)` is pure virtual. Constructs `VGMFile`s on pattern hits. |
| `VGMColl` | `components/VGMColl.h:VGMColl` | Bundle of one `VGMSeq` + N `VGMInstrSet` + N `VGMSampColl` + N `VGMMiscFile`. `attachSeq/attachInstrSet/attachSampColl`, `seq()`. |
| `Matcher` (abstract) | `components/matcher/Matcher.h:Matcher` | Associative layer. `onNewFile`/`onCloseFile` (variant over the four VGMFile kinds) dispatch to virtual `onNewSeq`/`onNewSampColl`/… |
| `AkaoMatcher` | `formats/Akao/AkaoMatcher.h:AkaoMatcher` | AKAO-specific grouping; maps `seq_id`/instrset-id/sampcoll, builds collections in `tryCreateCollection`. |
| `AkaoMatcher::tryCreateCollection` | `formats/Akao/AkaoMatcher.cpp:90` | Requires seq + instrset; sample collection **optional** (see §2.3). Builds `VGMColl`, calls `pRoot->loadVGMColl`. |
| `VGMSeq` | `components/seq/VGMSeq.h:VGMSeq` | The sequence model root. Owns `SeqTrack[]`. `parseHeader`, `parseTrackPointers`, `loadTracks`, `convertToMidi`, `saveAsMidi`, `ppqn`. |
| `SeqTrack` | `components/seq/SeqTrack.h:SeqTrack` | One track. `readEvent()` (format override) + ~200 `add*` event helpers. Holds `channel`, `pMidiTrack`, `readMode`, `transpose`, loop/return stacks. |
| `SeqTrack::readEvent` | `components/seq/SeqTrack.h:111` | **Virtual; the per-format opcode decoder.** The authoritative "what each opcode does" lives in format overrides. |
| `SeqEvent` (+ subclasses) | `components/seq/SeqEvent.h:SeqEvent` | Typed display/IR nodes: `NoteOnSeqEvent`, `DurNoteSeqEvent`, `VolSeqEvent`, `PanSeqEvent`, `TempoSeqEvent`, `ProgChangeSeqEvent`, `JumpSeqEvent`, `CallSeqEvent`, `ReturnSeqEvent`, `LoopForeverSeqEvent`, `TrackEndSeqEvent`, … |
| `ReadMode` | `components/seq/ReadMode.h:ReadMode` | `READMODE_ADD_TO_UI` / `READMODE_CONVERT_TO_MIDI` / `READMODE_FIND_DELTA_LENGTH`. Selects what the parser walk produces. |
| `VGMSeq::convertToMidi` | `components/seq/VGMSeq.cpp:76` | Two-walk conversion: FIND_DELTA_LENGTH to size tracks, then CONVERT_TO_MIDI to emit a `MidiFile`. |
| `SeqEventTimeIndex` | `components/seq/SeqEventTimeIndex.h` | Timeline index linking persisted SeqEvents to realized tick times during the convert pass (`VGMSeq::m_timedEvents`). |
| `MidiFile` | `conversion/MidiFile.h:MidiFile` | Output document. Owns `MidiTrack[]` + `globalTrack` + `globalTranspose`. `saveMidiFile`, `writeMidiToBuffer`, `setPPQN`. |
| `MidiTrack` | `conversion/MidiFile.h:MidiTrack` | One output track. `add*`/`insert*` emitters (`addNoteByDur`, `addVol`, `addPan`, `addControllerEvent`, `addTempoBPM`, …), `sort()`, `writeTrack`. |
| `MidiEvent` (+ subclasses) | `conversion/MidiFile.h:MidiEvent` | Output event nodes with `absTime`/`priority`; `writeEvent(buf,time)` emits bytes. `NoteEvent`, `ControllerEvent`, `ProgChangeEvent`, `PitchBendEvent`, `TempoEvent`, `TimeSigEvent`, `SysexEvent`, … |

### 2.2 SeqEvent / SeqTrack helper → MIDI event mapping

This table is the practical "what does this turn into" reference. Read it as: a format's
`readEvent()` calls the **SeqTrack helper**; under `READMODE_CONVERT_TO_MIDI` that helper routes
to the **MidiTrack emitter**, producing the **MIDI output**. Controller numbers below were read
directly from the `ControllerEvent` subclass constructors in `conversion/MidiFile.h` (and
`MidiFile.cpp` for reverb) and are **verified** unless tagged otherwise.

| SeqTrack helper | SeqEvent (UI node) | MidiTrack emitter / MidiEvent | MIDI output |
|---|---|---|---|
| `addNoteOn` / `addNoteByDur` | `NoteOnSeqEvent` / `DurNoteSeqEvent` | `MidiTrack::addNoteOn` / `addNoteByDur` → `NoteEvent` | Note On (0x90); note duration realized as a later Note Off |
| `addNoteOff` | `NoteOffSeqEvent` | `MidiTrack::addNoteOff` → `NoteEvent(bNoteDown=false)` | Note Off (0x80) / Note On vel 0 |
| `addVol` | `VolSeqEvent` | `VolumeEvent` | CC **7** (Channel Volume) |
| `addVol` (fine/14-bit path) | `Volume14BitSeqEvent` | `VolumeFineEvent` | CC **39** (Volume LSB) |
| `addExpression` | `ExpressionSeqEvent` | `ExpressionEvent` | CC **11** (Expression) |
| `addExpression` (fine) | — | `ExpressionFineEvent` | CC **43** (Expression LSB) |
| `addPan` | `PanSeqEvent` | `PanEvent` | CC **10** (Pan) |
| `addReverb` | `ReverbSeqEvent` | `MidiTrack::addReverb` (`MidiFile.cpp:443`) | CC **91** (Reverb send) |
| `addModulation` | `ModulationSeqEvent` | `ModulationEvent` | CC **1** (Mod wheel) |
| `addBreath` | `BreathSeqEvent` | `BreathEvent` | CC **2** (Breath) |
| `addSustainEvent` | `SustainSeqEvent` | `SustainEvent` | CC **64** (Sustain pedal) |
| `addPortamento` | `PortamentoSeqEvent` | `PortamentoEvent` | CC **65** (Portamento on/off) |
| `addPortamentoTime` | `PortamentoTimeSeqEvent` | `PortamentoTimeEvent` | CC **5** (Portamento time) |
| `addPortamentoTime14Bit` | — | `PortamentoTimeFineEvent` | CC **37** (Portamento time LSB) |
| `addPortamentoControlNoItem` | — | `PortamentoControlEvent` | CC **84** (Portamento control) |
| `addLegatoPedalNoItem` | — | `LegatoPedalEvent` | CC **68** (Legato footswitch) |
| `addBankSelect` | `BankSelectSeqEvent` | `BankSelectEvent` / `BankSelectFineEvent` | CC **0** (MSB) / CC **32** (LSB) |
| `addMonoNoItem` | — | `MonoEvent` | CC **126** (Mono mode) |
| `addProgramChange` | `ProgChangeSeqEvent` | `ProgChangeEvent` | Program Change (0xC0) |
| `addPitchBend` | `PitchBendSeqEvent` | `PitchBendEvent` | Pitch Bend (0xE0) |
| `addChannelPressure` | `ChannelPressureSeqEvent` | `ChannelPressureEvent` | Channel Pressure (0xD0) |
| `addMasterVol` | `MastVolSeqEvent` | `MasterVolEvent` (a `SysexEvent`) | Universal SysEx Master Volume (`7F 7F 04 01 …`) |
| `addTempo` / `addTempoBPM` | `TempoSeqEvent` | `TempoEvent` | Meta Set Tempo (FF 51 03) |
| `addTimeSig` | `TimeSigSeqEvent` | `TimeSigEvent` | Meta Time Signature (FF 58) |
| `addEndOfTrack` | `TrackEndSeqEvent` | `EndOfTrackEvent` | Meta End of Track (FF 2F) |
| `addMarker` | `MarkerSeqEvent` | `MarkerEvent` | **Internal only** — `MarkerEvent::writeEvent` returns time unchanged, i.e. nothing is written to the SMF; used to drive other logic / UI. |
| `addGlobalTranspose` | — | `GlobalTransposeEvent` | **Internal only** — adjusts subsequent note keys (`MidiFile::globalTranspose` / `MidiTrack::activeNotes`); not emitted as bytes. |
| `addPitchBendRange` | `PitchBendRangeSeqEvent` | `MidiTrack::addPitchBendRange` | RPN 0 sequence (CC 101/100/6/38) **(unverified — emitter exists; exact CC sequence not re-read)** |
| `addFineTuning` / `addCoarseTuning` | `FineTuningSeqEvent` / `CoarseTuningSeqEvent` | `MidiTrack::addFineTuning` / `addCoarseTuning` | RPN 1 / RPN 2 sequences **(unverified — exact CC bytes not re-read)** |
| `addModulationDepthRange` | `ModulationDepthRangeSeqEvent` | `MidiTrack::addModulationDepthRange` | RPN 5 sequence **(unverified)** |
| GM/GS/XG resets | — | `GMResetEvent`/`GM2ResetEvent`/`GSResetEvent`/`XGResetEvent` (`SysexEvent`) | Corresponding reset SysEx (verified byte arrays in `MidiFile.h`) |

`MidiEventType` (`conversion/MidiFile.h:34`) is the runtime tag enum returned by
`MidiEvent::eventType()`; note several events deliberately share `MIDIEVENT_VOLUME` /
`MIDIEVENT_EXPRESSION` etc. across coarse/fine variants.

### 2.3 Invariants, gotchas & oracle notes

1. **Three-mode parser, not IR-then-render.** The single biggest gotcha: MIDI is produced by
   *re-walking the parser* in `READMODE_CONVERT_TO_MIDI`, not by traversing the `SeqEvent` list.
   The `SeqEvent` tree is built in `READMODE_ADD_TO_UI`. The `add*` helpers branch on
   `SeqTrack::readMode` (and the `*NoItem` variants gate MIDI emission on
   `READMODE_CONVERT_TO_MIDI`). When auditing parity, trace the **format's `readEvent()` →
   `add*` → `MidiTrack::add*`** path, not the SeqEvent constructors.

2. **Tick resolution (PPQN).** Output resolution is per-sequence: `VGMSeq::setPPQN(u16)` /
   `VGMSeq::ppqn()`, propagated to `MidiFile::setPPQN`. The value is set by each format's parser;
   there is no single global constant. **(The default PPQN value is unverified — it is whatever
   the format parser supplies; GME's own 48-native/96-MIDI convention is a GME thing, not a
   vgmtrans guarantee.)** MIDI events store **absolute** tick times (`MidiEvent::absTime`); delta
   times are computed only at serialization (`MidiTrack::writeTrack` / `MidiFile::writeMidiToBuffer`).

3. **Event ordering is priority-based.** Simultaneous events at the same tick are ordered by
   `MidiEvent::priority` (`PRIORITY_HIGHEST … PRIORITY_LOWEST`, `MidiFile.h:26`) via
   `MidiTrack::sort()` before write. Resets are `PRIORITY_HIGHEST`; bank-select is `PRIORITY_HIGH`
   so it precedes program change; master volume is `PRIORITY_HIGHER`. Mis-ordered output usually
   means a wrong priority, not a wrong tick.

4. **Loops are control-flow state, expanded at convert time.** Loop/branch opcodes become
   `JumpSeqEvent` / `CallSeqEvent` / `ReturnSeqEvent` / `LoopForeverSeqEvent` (display) plus
   parser state on `SeqTrack`: `loopStack` (`LoopState{endOffset, remainingCount}`),
   `returnOffsets`, and `infiniteLoops`. Actual loop *expansion* into repeated MIDI happens during
   conversion, governed by `ConversionContext::sequenceLoops`
   (`ConversionContext.h:31`): the tick-by-tick loader replays until
   `foreverLoopCount() >= sequenceLoops + 1` playthroughs (`VGMSeq.cpp:248`). Infinite-loop
   protection uses a hashed `ControlFlowState` visited-set (`SeqTrack.h:57`,
   `shouldTrackControlFlowState`). **Contrast with GME**, which computes intro/loop times up front
   and renders `intro + 2 × loop`; vgmtrans instead *plays* the loops via the loader.

5. **Two loader styles.** `VGMSeq::bLoadTickByTick` switches between (a) tick-synchronized loading
   of all tracks in lockstep (needed when tracks share state or for loop counting) and (b) simple
   track-by-track loading to `stopTime` (`VGMSeq.cpp:193` vs `:255`). AKAO uses the loop-counting
   path. This is a per-format flag.

6. **Matcher: sample collection is OPTIONAL (PR #914 / GME bead `6pf`).**
   `AkaoMatcher::tryCreateCollection` (`AkaoMatcher.cpp:90`) requires a seq **and** an instrument
   set, but a **sample collection is not required** — a sequence recovered without its sample bank
   (e.g. a standalone AKAO sequence block, or a PSF that optimized out sample ids) still forms a
   `VGMColl` and **still exports MIDI**; only SF2/DLS export ends up with no samples. The matcher
   associates sample collections by articulation-id coverage: it collects the `artNum`s required
   by the instrument set's regions and greedily attaches the `AkaoSampColl`s whose
   `[starting_art_id, starting_art_id + nNumArts)` ranges cover them, always also including the
   sampcoll whose `id()` equals `seq->id()` (handles key-split program changes that reference an
   articulation directly — comment cites FF8 song 106). Upstream ref:
   **vgmtrans PR #914 "AkaoMatcher: allow collections without a sample collection."**

7. **PSF files are treated as self-contained.** After a scan, if the source `RawFile` is a
   `psf`/`minipsf`/`psflib` (`AkaoMatcher::isPsfFile`), all of its detected files are erased from
   future match consideration (`AkaoMatcher.cpp:31`) — each PSF is assumed to carry exactly its
   own collection.

8. **Channel groups exceed 16 channels.** `SeqTrack::channelGroup` + `MidiPortEvent` route track
   groups across multiple MIDI ports, so a sequence with >16 tracks does not collapse onto 16
   channels.

9. **AKAO format reference.** Header offsets / opcode semantics for AkaoSeq are documented (in
   Japanese) at the SaGa Frontier wiki: <https://w.atwiki.jp/sagafrontier/pages/43.html>. The
   authoritative *vgmtrans* interpretation of those opcodes is `formats/Akao/AkaoSeq`'s
   `readEvent()` override (not read in detail for this doc — out of scope, but it is the next stop
   when chasing an AKAO opcode parity question).

---

## Appendix — files read for this document

`io/RawFile.h`, `components/Scanner.h`, `components/VGMColl.h`/`.cpp`,
`components/matcher/Matcher.h`, `formats/Akao/AkaoMatcher.h`/`.cpp`,
`components/seq/VGMSeq.h`, `components/seq/VGMSeq.cpp` (convertToMidi/loadTracks region),
`components/seq/SeqTrack.h`, `components/seq/SeqTrack.cpp` (representative `add*` helpers),
`components/seq/SeqEvent.h`, `components/seq/ReadMode.h`,
`conversion/MidiFile.h`, `conversion/MidiFile.cpp` (addReverb), `ConversionContext.h`.

Anything tagged **(unverified)** above was inferred from emitter names / signatures without
re-reading the byte-level `MidiFile.cpp` implementation; confirm there before relying on it for a
parity fix.
