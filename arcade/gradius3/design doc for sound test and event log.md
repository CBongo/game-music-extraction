I want to create a lua script or plugin for the MAME arcade emulator that will allow me to create a "sound test" mode for Gradius 3.

MAME script docs:  https://docs.mamedev.org/luascript/index.html  

The primary goals of the sound test mode are to allow me to:
 - Audition a specific sound event for identification
 - Selectively enable and disable specific sound channels in a song to isolate instruments (like a "mute"/"solo" feature) while the song is playing

For sound test mode, the main cpu should be made not to interfere with the sound test.  This could be done by suspending the maincpu emulation (since the sound test only needs the audiocpu emulation running; the game display is not necessary - the subcpu could be suspended as well), or preventing the maincpu from writing to the sound latch and triggering the audiocpu interrupt.

An on-screen display user interface is needed to select a sound event and to trigger it.  Sound events are represented by a byte value shown as a two-digit hex number.  This could be typed in or "dialed" in depending on what is most convenient from a developer viewpoint.

The bulk of the on-screen display could show the status of the sound system; of particular interest would be the audio chip state for the YM2151 (most interesting) and K007232 (less interesting), and the sound driver voice state (an array of 10 voice state structures of length 0x50 starting at 0xf800 in the audio chip's RAM space; a partial description is in eventstate.h which could help with labeling).

There should be an interface to show the 10 audio channels (numbered 0-9) and allow selective mute and/or solo of each channel as the song is playing.  This could be a row of boxes, one for each channel, that can be clicked or selected by keyboard to toggle mute on/off.  Some ways to achieve the channel muting effect would be to directly affect the chip emulation or call methods on the sound chips and/or mixer/output devices, or to set field 0x3f in the voice state structure for the voice to 1 to mute and 0 to unmute.

A secondary goal would be to have a sound event "listener" script or plugin that displays the sound events that the main cpu requests as they happen on an on-screen display (or seperate window if that's convenient) in real time during gameplay.  This is meant to allow me to associate the sound events with a description of what sound or song is produced; for example event 0x51 produces the voice sample "laser", while event 0x01 produces the "player shooting" sound effect, and event 0x81 plays the stage one background music.
This should be a listing of the most recently requested sound events in a vertical display on the right side of the screen.  Events should be displayed as two hex digits representing the event byte sent in the sound latch (main cpu 0xe8000); for example "01" for event 0x01.  The listing could be split into separate sections for sound effects (0x01-0x4f), voice samples (0x50-7f), and music (0x80-0xfe).  Events 0x00 and 0xff should be omitted as they cancel existing effects.

For both functions (sound test mode and the sound event listener), I would like suggestions on whether a plugin or separate lua script would be the best approach to take.  They should probably be developed as separate programs since they have very different interface needs (standalone vs. in-game), but they could share some common code if it makes sense.