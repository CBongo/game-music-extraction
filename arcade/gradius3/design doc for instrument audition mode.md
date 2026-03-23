In order to determine appropriate patch mappings and improve the accuracy of percussion rendering, I want to implement a way to audition the "instruments" defined in Gradius 3 arcade, both for the FM instruments and sampled instruments.

The high-level plan is to implement a MAME lua script similar to g3_soundtest.lua that effectively halts the CPU and presents a UI that allows selecting an instrument, then playing notes with it.  There may be a need to switch "modes" between FM and sampled.  Notes should be playable using the keyboard with Q playing C, 2 playing C#, W playing D, 3 playing D#, etc.  Octave should be selectable with + and - keys, and default to something reasonable (4?) when selecting an instrument.  Selecting an instrument should display its parameters on-screen (the parameters to display will differ between FM and sampled).  Notes should be played while the key is held down; if that's not easy to accomplish an alternative is to key on the note for about a second or so (or a quarter note if that's easier to handle).  The calculated key code (for FM) or address skip (for sampled) should be displayed when a note is played and stay onscreen until another note is played.  The instrument should be selectable via keyboard, perhaps with up/down arrow or a text box (when focused, it should have priority over the number keys as part of the note playing).  Instrument numbers should be represented as two-digit hex numbers.

I see a couple of ways to generate the sounds.  One is to simulate a song in ROM or RAM and cause the existing audio ROM code to execute it as if playing a song.  The other is to directly program the sound chips based on the ROM data.  I believe the simpler approach will be the latter, so I'm leaning towards that as a first cut at implementation.

The sequence data uses the same opcode, 0xe2, to represent a Patch Change for both FM (YM2151, channels 0-7) and samples (K007232, channels 8-9).  

For FM, the operand is an index to a table of FM properties to set for the instrument.  The index references an array of pointers at 0x27cd in the audio ROM which in turn point to the actual FM properties structure - see fmprops.h for the structure format.  The values will typically need to be shifted around when setting the 2151 registers to get them in the proper bit positions.  The instrument table for FM has 178 entries.

For samples, there are two modes of operation that are used, depending on vstate 1f.

The first, selected when vstate 1f is zero, uses the Patch Change opcode like the FM. The operand is an index to a table of 7232 properties to set for the instrument.  The index directly references a table at 0x3a44 of the properies; see k7232props.h for the specific fields.  The table has 7 elements, as the patch change for samples has operands that range from 0-6.  When a note is played in this mode, the address step register is set from a table of words at 0x1d1f indexed on the note number (low nybble of the note command plus octave adjustment, global and per voice transpose, and vcmd EC transpose; for our purposes here, only the note and octave are needed).  

The second mode, when vstate 1f is nonzero, sets the k7232 props at note playing time.  The low nybble of the note command is used directly (no octave or transpose adjustments) as an index to a table of k7232 props determined by vstate 1f.  These tables are based at 0x3a83 and the indexing looks like this:

props_to_use = props_tbls_3a83[(note & 0xf) * 9 + (vstate_1f - 1) * 9 * 15]

There are 7 tables, so vstate 1f can range from 1-7 in this mode.  The bank, addr, and addr_step are all set from the k7232 props table entry.

The UI should allow both methods to work, by having an element that allows for setting the vstate 1f value.  The proper algorithm should be used to play the 7232 sample based on its setting.  This can be a simple up/down counter - maybe using pgup/pgdown keys to change it.