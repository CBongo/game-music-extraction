The goal of this subproject of game music extraction is to take Sidplayer .mus files and transcribe them to musical notation by creating reasonably faithful MIDI files that can be imported into music publishing software like MuseScore.

The file format is documented here:  https://github.com/MyDeveloperThoughts/ComputeSidPlayerC64Source/blob/main/notes/musFileFormat.md  That repo also contains the reverse-engineered source code of the Sidplayer program itself and would be a  good source of specific information.

The Compute's Gazette Sidplayer Collection is available locally in M:\Music\c64\CGSC which provides lots of sample input files for testing.  The particular song I am most interested in is M:\Music\c64\CGSC\Harry_Bratt\For_the_Times.mus

If I were to do this myself, I would probably do something similar to my other game extraction programs in this repo, using a perl script from the command line to walk through the input file and generate a MIDI file based on the events in the input.  In this case, the file format is already pretty well documented, so reverse engineering the format is not important.  So the text disassembly is not really needed here.  Using perl is not required either, though I have a slight preference for perl or python.  Use language provided modules wherever appropriate, in particular for reading and writing MIDI date (e.g. perl-midi).

Compose a plan to implement a Sidplayer converter to MIDI format, taking as input one (or more, possibly as a future improvement) Sidplayer filename as input and writing a MIDI file with the same base name but with a .mid instead of .mus extension.  The most important aspects to get correct are note values (reflecting transpose and similar commands) and durations (reflecting utility durations etc.).  This also implies that structural functions (repeat, call) need to be implemented.

A selectable option should choose whether to map the C64 SID voices to MIDI tracks, or to attempt to detect "instruments" (defined loosely as a set of waveform, ADSR, and related timbre settings) across voices and map the instruments to MIDI tracks instead.  We'll implement the SID voice mapping first for simplicity.

The program is intended to be run by hand against a set of input files that aren't changing over time; they are largely historical.  So there is no need to worry about maintaining compatibility of output or thorough testing; if something goes wrong, we just run the script again to get better output.

I generally prefer fairly verbose status reporting, and here it is appropriate to provide this output.  A command line switch to silence output except for errors would be appropriate, though (off by default).