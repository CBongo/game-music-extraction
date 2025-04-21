# game-music-extraction
Scripts etc. used for extracting music sequence data from console and home computer games.

Organized by system.

The scripts are pretty much all in Perl, and make extensive use of the perl-midi package to handle
reading/writing MIDI files.

If I've analyzed a game enough, I'll have a file named `<game>mus.pl` that when run reads a ROM or disk image
and generates a set of General MIDI files (in the mid/ directory) that attempt to represent the original sequenced
music for the game.  In some cases it also generates a set of text files (in the txt/ directory) that are
a sort of "disassembly" view of the sequence data.  In other cases, this is done with a separate
`<game>mustxt.pl` script instead.

I've also pulled in data from other sources, such as OST track listings to get "official" song names.

Some of the scripts are pretty far along and yield listenable results for the MIDI files.  Others are just far
enough along to get basic note data and may not have reasonable patch mapping yet and sound horrendous.

ROMs and disk images are not included for copyright reasons.  It's the internet; they're not hard to track
down yourself.  Check the filenames and comments in the scripts to find out which version I used; in most cases
it's the US/North American release.  A couple of them are done off fan patches to the Japanese version; assuming
the patch isn't too intrusive, the original JP ROM will probably work OK too.

My notes on the game code can usually be found in a notes.txt or work.txt, and in disassembly files (.dis) as
comments.

My personal goal for this project has been to identify the source sequence data in order to produce automated,
note-exact sheet music for game music, and generally to understand and appreciate the specific arrangements
that game composers and sound designers have used to make the music that I have loved since I was a child.
That, and I can't help but want to take things apart to see how they work :)

Feedback or assistance is welcomed.  I'm not hard to find.

 -- Bongo
