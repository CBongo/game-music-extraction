#!/usr/bin/perl
#
# mus2mid.pl - Convert Sidplayer .mus files to MIDI format
# by Claude under direction from Christopher Bongaarts - 2026-04-12
#
# Reads COMPUTE's Sidplayer .mus files and writes MIDI files.
# Format documented at:
#   https://github.com/CBongo/ComputeSidPlayerC64Source/blob/main/notes/musFileFormat.md
#   https://modland.com/pub/documents/format_documentation/Sidplayer%20type%20B%20%28.mus%29.txt
#
# Usage: perl mus2mid.pl [--quiet|-q] file.mus [file2.mus ...]

use strict;
use warnings;
use MIDI;

# -------------------------------------------------------------------------
# Constants
# -------------------------------------------------------------------------

my $TICKS_PER_QUARTER = 480;

# MIDI ticks for each duration value (index = bits 2-4 of command byte)
# Index: 0=64th, 1=utility(placeholder), 2=whole, 3=half, 4=quarter,
#        5=8th, 6=16th, 7=32nd
my @DUR_TICKS = (30, 0, 1920, 960, 480, 240, 120, 60);

# Default tempo byte (MM 100 per the Sidplayer book defaults)
my $DEFAULT_TEMPO = 0x90;  # 144 decimal, BPM = 14400/144 = 100

# CIA timer base for NTSC (used by JIF command)
my $CIA_BASE_NTSC = 0x4295;
my $CLOCK_NTSC    = 1_022_727;  # Hz

# Semitone offsets for note names (index 1-7: C D E F G A B)
my @NOTE_SEMITONES = (0, 0, 2, 4, 5, 7, 9, 11);

# Default note velocity
my $DEFAULT_VELOCITY = 100;

# Maximum phrase nesting depth
my $MAX_CALL_DEPTH = 5;

# -------------------------------------------------------------------------
# Globals
# -------------------------------------------------------------------------

my $quiet = 0;
my $disasm = 0;
my $disasm_bytes = '';  # set before each vprintf to embed raw bytes in disasm mode

# Global phrase table (shared across all voices): phrase_num => byte offset
# into the combined voice data where the phrase body starts (after DEF cmd)
my @phrases;

# Combined raw voice data buffer (all 3 voices concatenated after the header)
my $vdata;

# -------------------------------------------------------------------------
# Main
# -------------------------------------------------------------------------

# Parse flags
while (@ARGV && $ARGV[0] =~ /^-/) {
    my $flag = shift @ARGV;
    if ($flag eq '--quiet' || $flag eq '-q') {
        $quiet = 1;
    } elsif ($flag eq '--disasm' || $flag eq '-d') {
        $disasm = 1;
    } else {
        die "Unknown flag: $flag\n";
    }
}

die "Usage: $0 [--quiet|-q] [--disasm|-d] file.mus [file2.mus ...]\n" unless @ARGV;

for my $infile (@ARGV) {
    vprintf("Processing: %s\n", $infile);
    eval { process_file($infile) };
    if ($@) {
        warn "ERROR processing $infile: $@\n";
    }
}

exit 0;

# -------------------------------------------------------------------------
# File processing
# -------------------------------------------------------------------------

sub process_file {
    my ($infile) = @_;

    # Determine output filename: same basename as input, .mid extension, in current directory
    (my $basename = $infile) =~ s{.*[\\/]}{};  # strip directory
    (my $outfile = $basename) =~ s/\.[Mm][Uu][Ss]$/.mid/
        or die "Input file must have .mus extension: $infile\n";

    # Read the whole file
    open my $fh, '<', $infile or die "Cannot open $infile: $!\n";
    binmode $fh;
    local $/;
    my $buf = <$fh>;
    close $fh;

    my $filelen = length($buf);
    vprintf("  File size: %d bytes\n", $filelen);

    die "File too short\n" if $filelen < 8;

    # Parse header: 2-byte load address + three 2-byte voice sizes (little-endian)
    my ($loadaddr, $v1sz, $v2sz, $v3sz) = unpack('vvvv', $buf);
    vprintf("  Load address: \$%04X (ignored)\n", $loadaddr);
    vprintf("  Voice sizes: %d, %d, %d bytes\n", $v1sz, $v2sz, $v3sz);

    my $total_voice = $v1sz + $v2sz + $v3sz;
    die sprintf("Voice sizes (%d) exceed file size (%d)\n", $total_voice + 8, $filelen)
        if $total_voice + 8 > $filelen;

    # Extract voice data blocks (combined into one buffer for phrase addressing)
    $vdata = substr($buf, 8, $total_voice);

    # Verify HLT terminators ($01 $4F) at end of each voice
    my @vstarts = (0, $v1sz, $v1sz + $v2sz);
    my @vlens   = ($v1sz, $v2sz, $v3sz);
    for my $v (0..2) {
        if ($vlens[$v] >= 2) {
            my $last2 = substr($vdata, $vstarts[$v] + $vlens[$v] - 2, 2);
            my ($b1, $b2) = unpack('CC', $last2);
            unless ($b1 == 0x01 && $b2 == 0x4F) {
                warn sprintf("  WARNING: Voice %d does not end with HLT (\$01\$4F), got \$%02X\$%02X\n",
                             $v, $b1, $b2);
            }
        }
    }

    # Extract description text (NULL-terminated, PETSCII, after voice data)
    my $desc_offset = 8 + $total_voice;
    my $desc_raw = substr($buf, $desc_offset);
    $desc_raw =~ s/\x00.*//s;  # trim at NULL
    $desc_raw =~ s/\x0D/\n/g;  # PETSCII CR -> newline
    $desc_raw =~ s/[^\x20-\x7E\n]//g;  # strip non-printable
    if ($desc_raw =~ /\S/) {
        vprintf("  Description:\n");
        for my $line (split /\n/, $desc_raw) {
            vprintf("    %s\n", $line) if $line =~ /\S/;
        }
    }

    # Initialize phrase table (filled in as DEF commands are executed)
    @phrases = (undef) x 24;

    # Set up MIDI structures
    my $opus = MIDI::Opus->new({'format' => 1, 'ticks' => $TICKS_PER_QUARTER});
    my $ctrack = MIDI::Track->new;
    push @{$opus->tracks_r}, $ctrack;

    # Shared state: values that affect all voices (tempo, jiffy rate, UTL)
    my $shared = {
        tempo       => $DEFAULT_TEMPO,
        jiffy_usec  => 1_000_000 / 60,   # default ~16667 usec/jiffy
        utl_jiffies => 0,                 # UTL affects all voices
    };

    my @cevents = (
        ['time_signature', 0, 4, 2, $TICKS_PER_QUARTER, 8],
        ['set_tempo', 0, calc_tempo_usec($shared->{tempo}, $shared->{jiffy_usec})],
    );

    # Per-voice score event lists
    my @e;
    for my $v (0..2) {
        $e[$v] = [['track_name', 0, "Voice $v"]];
    }

    # Initialize per-voice state
    my @vs;
    for my $v (0..2) {
        $vs[$v] = {
            v            => $v,
            pos          => $vstarts[$v],
            vstart       => $vstarts[$v],
            vend         => $vstarts[$v] + $vlens[$v],
            totaltime    => 0,
            transpose    => 0,
            velocity     => $DEFAULT_VELOCITY,
            vol          => 15,
            utv_jiffies  => 0,
            pnt_jiffies  => 4,
            hld_jiffies  => 0,
            repeat_pos   => undef,
            repeat_count => undef,
            call_stack   => [],
            defining     => [],   # stack of phrase numbers currently being defined
            note_count   => 0,
            done         => 0,
            tie_note_ev  => undef,  # pending tied note score event ref
            tie_pitch    => undef,  # MIDI pitch of pending tied note
            abs_pitch    => undef,  # pitch set by absolute pitch command (cmd==0x00)
            abs_velocity => undef,  # velocity for next abs-pitch note (stepped down each set)
        };
    }

    # Parallel processing: always advance the voice with the earliest totaltime.
    # This ensures DEF commands become visible to CALLs in proper temporal order,
    # even when the DEF is on a different voice from the CALL.
    my $max_iters = 2_000_000;
    my $iters = 0;
    while ($iters++ < $max_iters) {
        my @active = grep { !$vs[$_]{done} } 0..2;
        last unless @active;
        # Pick earliest voice; break ties by voice number for determinism
        my $v = (sort { $vs[$a]{totaltime} <=> $vs[$b]{totaltime} || $a <=> $b } @active)[0];
        advance_voice($vs[$v], $shared, \@e, \@cevents);
    }
    if ($iters > $max_iters) {
        warn "  WARNING: hit parallel processing safety limit ($max_iters iterations)\n";
    }

    for my $v (0..2) {
        vprintf("  Voice %d: %d notes, %d MIDI ticks total\n",
                $v, $vs[$v]{note_count}, $vs[$v]{totaltime});
    }

    # Build MIDI tracks
    $ctrack->events_r((MIDI::Score::score_r_to_events_r(\@cevents))[0]);
    for my $v (0..2) {
        my ($evs) = MIDI::Score::score_r_to_events_r($e[$v]);
        push @{$opus->tracks_r}, MIDI::Track->new({'events_r' => $evs});
    }

    $opus->write_to_file($outfile);
    vprintf("  Written: %s\n", $outfile);
}

# -------------------------------------------------------------------------
# Advance one voice by one note-worth of commands.
# Processes non-note commands in a loop, then processes one note/rest and
# returns so the scheduler can pick the next voice.
# -------------------------------------------------------------------------

sub advance_voice {
    my ($s, $shared, $e, $cevents) = @_;
    my $v = $s->{v};

    while ($s->{pos} < $s->{vend}) {
        my ($cmd, $opt) = unpack('CC', substr($vdata, $s->{pos}, 2));
        my $next_pos = $s->{pos} + 2;
        $disasm_bytes = sprintf("%02X %02X  ", $cmd, $opt);
        my $cmdtype  = $cmd & 0x03;

        # ---- Absolute pitch set (cmd == 0x00, duration index 0) ----------
        if ($cmd == 0x00) {
            my $note_idx   = $opt & 0x07;
            my $oct_enc    = ($opt >> 3) & 0x07;
            my $accidental = ($opt >> 6) & 0x03;
            my $octave     = $oct_enc ^ 0x07;
            my $pitch = note_to_midi($note_idx, $accidental, $octave, $s->{transpose});
            # Step velocity down each time; start from current voice velocity
            my $vel = defined $s->{abs_velocity} ? $s->{abs_velocity} : $s->{velocity};
            $vel = int($vel * 0.75);
            $vel = 1 if $vel < 1;
            $s->{abs_pitch}    = $pitch;
            $s->{abs_velocity} = $vel;
            vprintf("    [v%d pos=%04X]  ABS %s oct=%d pitch=%d vel=%d\n",
                    $v, $s->{pos}, note_name($note_idx, $accidental, $octave, $s->{transpose}),
                    $octave, $pitch, $vel);
            $s->{pos} = $next_pos;
            next;  # no duration, keep processing
        }

        # ---- NOTE command (bits 0-1 = 00) --------------------------------
        if ($cmdtype == 0) {
            my $dur_idx  = ($cmd >> 2) & 0x07;
            my $dotted   = ($cmd >> 5) & 0x01;
            my $tie      = ($cmd >> 6) & 0x01;
            my $dbl_or_t = ($cmd >> 7) & 0x01;  # dbl-dotted if dotted; triplet if not

            # Note/octave/accidental from option byte
            my $note_idx   = $opt & 0x07;
            my $oct_enc    = ($opt >> 3) & 0x07;
            my $accidental = ($opt >> 6) & 0x03;
            my $octave     = $oct_enc ^ 0x07;

            # Compute duration in MIDI ticks
            my $dur_ticks;
            if ($dur_idx == 1) {
                # Utility duration: bit 5 selects UTL(0) vs UTV(1)
                my $jiffies = $dotted ? $s->{utv_jiffies} : $shared->{utl_jiffies};
                $dur_ticks = jiffies_to_ticks($jiffies, $shared->{tempo}, $shared->{jiffy_usec});
                vprintf("    [v%d pos=%04X]  UTL-note %s (%.1f jiffies = %d ticks)\n",
                        $v, $s->{pos}, note_name($note_idx, $accidental, $octave, $s->{transpose}),
                        $jiffies, $dur_ticks) unless $note_idx == 0;
            } else {
                $dur_ticks = $DUR_TICKS[$dur_idx];
                if ($dotted && $dbl_or_t) {
                    $dur_ticks = int($dur_ticks * 7 / 4);  # double-dotted
                } elsif ($dotted) {
                    $dur_ticks = int($dur_ticks * 3 / 2);  # dotted
                } elsif ($dbl_or_t) {
                    $dur_ticks = int($dur_ticks * 2 / 3);  # triplet
                }
            }

            if ($note_idx == 0) {
                # Rest: any pending tie ends here; sound duration stays as-is
                if (defined $s->{tie_note_ev}) {
                    vprintf("    [v%d pos=%04X]  (tie ended by rest)\n", $v, $s->{pos});
                    $s->{tie_note_ev} = undef;
                    $s->{tie_pitch}   = undef;
                }
                if (defined $s->{abs_pitch}) {
                    # Simulate release-phase pitch change: emit/extend note at abs_pitch
                    my $pitch = $s->{abs_pitch};
                    my $vel   = $s->{abs_velocity};
                    if (@{$e->[$v]} && $e->[$v][-1][0] eq 'note'
                            && $e->[$v][-1][4] == $pitch) {
                        # Same pitch as current abs note: extend it (tie-like)
                        $e->[$v][-1][2] += $dur_ticks;
                        vprintf("    [v%d pos=%04X]  ABS-REST (extend pitch=%d) +%d ticks\n",
                                $v, $s->{pos}, $pitch, $dur_ticks);
                    } else {
                        # New pitch: emit a new note
                        vprintf("    [v%d pos=%04X]  ABS-REST pitch=%d vel=%d dur=%d ticks\n",
                                $v, $s->{pos}, $pitch, $vel, $dur_ticks);
                        push @{$e->[$v]}, ['note', $s->{totaltime}, $dur_ticks, $v, $pitch, $vel];
                        $s->{note_count}++;
                    }
                    # Keep abs_pitch alive so subsequent rests continue extending
                } else {
                    vprintf("    [v%d pos=%04X]  REST %d ticks\n", $v, $s->{pos}, $dur_ticks);
                }
            } else {
                my $pitch = note_to_midi($note_idx, $accidental, $octave, $s->{transpose});
                $s->{abs_pitch}    = undef;
                $s->{abs_velocity} = undef;

                if (defined $s->{tie_note_ev}) {
                    if ($pitch == $s->{tie_pitch}) {
                        # Same pitch: true tie — extend the pending note's duration and sound
                        $s->{tie_note_ev}[2] += $dur_ticks;
                        vprintf("    [v%d pos=%04X]  TIE (same pitch=%d) +%d ticks\n",
                                $v, $s->{pos}, $pitch, $dur_ticks);
                        if ($tie) {
                            # Still tied; keep pending
                        } else {
                            $s->{tie_note_ev} = undef;
                            $s->{tie_pitch}   = undef;
                        }
                        $s->{totaltime} += $dur_ticks;
                        $s->{pos} = $next_pos;
                        return;
                    } else {
                        # Different pitch: slur — end previous note exactly at this note's start
                        $s->{tie_note_ev}[2] = $s->{totaltime} - $s->{tie_note_ev}[1];
                        vprintf("    [v%d pos=%04X]  SLUR (prev pitch=%d -> new pitch=%d)\n",
                                $v, $s->{pos}, $s->{tie_pitch}, $pitch);
                        $s->{tie_note_ev} = undef;
                        $s->{tie_pitch}   = undef;
                    }
                }

                # Emit note (with or without tie)
                my $pnt_ticks = jiffies_to_ticks($s->{pnt_jiffies}, $shared->{tempo}, $shared->{jiffy_usec});
                my $hld_ticks = jiffies_to_ticks($s->{hld_jiffies}, $shared->{tempo}, $shared->{jiffy_usec});
                $hld_ticks = $dur_ticks if $hld_ticks > $dur_ticks;
                my $sound_ticks;
                if ($tie) {
                    # Tied note: hold for full duration; next note will adjust if slur
                    $sound_ticks = $dur_ticks;
                } else {
                    $sound_ticks = $dur_ticks - $pnt_ticks;
                    $sound_ticks = $hld_ticks if $sound_ticks < $hld_ticks;
                    $sound_ticks = 1          if $sound_ticks < 1;
                }
                vprintf("    [v%d pos=%04X]  %s %s oct=%d pitch=%d sound=%d dur=%d ticks\n",
                        $v, $s->{pos}, ($tie ? 'NOTE(tie)' : 'NOTE'),
                        note_name($note_idx, $accidental, $octave, $s->{transpose}),
                        $octave, $pitch, $sound_ticks, $dur_ticks);
                my $ev = ['note', $s->{totaltime}, $sound_ticks, $v, $pitch, $s->{velocity}];
                push @{$e->[$v]}, $ev;
                $s->{note_count}++;
                if ($tie) {
                    $s->{tie_note_ev} = $ev;
                    $s->{tie_pitch}   = $pitch;
                }
            }
            $s->{totaltime} += $dur_ticks;
            $s->{pos} = $next_pos;
            return;  # yield to scheduler after each note/rest
        }

        # ---- cmd byte = 0x01: SID/structural commands --------------------
        if ($cmd == 0x01) {
            my $lo4 = $opt & 0x0F;
            my $hi4 = ($opt >> 4) & 0x0F;

            if ($opt == 0x4F) {
                # HLT - halt this voice
                vprintf("    [v%d pos=%04X]  HLT\n", $v, $s->{pos});
                $s->{done} = 1;
                return;

            } elsif ($opt == 0x0F) {
                # TAL - repeat tail
                if (!defined $s->{repeat_pos}) {
                    warn sprintf("  WARNING: Voice %d TAL with no HED at pos %04X; repeating from start\n",
                                 $v, $s->{pos});
                    $s->{repeat_pos}   = $s->{vstart};
                    $s->{repeat_count} = 1;
                }
                if ($s->{repeat_count} == 0) {
                    # Infinite repeat: already capped, just continue
                    vprintf("    [v%d pos=%04X]  TAL (infinite, done)\n", $v, $s->{pos});
                    $s->{repeat_pos} = undef;
                    $s->{pos} = $next_pos;
                } elsif ($s->{repeat_count} > 0) {
                    $s->{repeat_count}--;
                    if ($s->{repeat_count} > 0) {
                        vprintf("    [v%d pos=%04X]  TAL (repeat, %d left)\n",
                                $v, $s->{pos}, $s->{repeat_count});
                        $s->{pos} = $s->{repeat_pos};
                    } else {
                        vprintf("    [v%d pos=%04X]  TAL (done) t=%d\n", $v, $s->{pos}, $s->{totaltime});
                        $s->{repeat_pos} = undef;
                        $s->{pos} = $next_pos;
                    }
                }
                # Yield after TAL so other voices can catch up to the same song
                # position (e.g. execute a DEF that this voice's CALL will need).
                return;

            } elsif ($opt == 0x2F) {
                # END - end phrase definition or return from call
                if (@{$s->{defining}}) {
                    my $phrase = pop @{$s->{defining}};
                    vprintf("    [v%d pos=%04X]  END (phrase %d defined)\n",
                            $v, $s->{pos}, $phrase);
                }
                if (@{$s->{call_stack}}) {
                    my $ret = pop @{$s->{call_stack}};
                    vprintf("    [v%d pos=%04X]  END (return from CALL to %04X)\n",
                            $v, $s->{pos}, $ret);
                    $s->{pos} = $ret;
                    next;
                }
                $s->{pos} = $next_pos;
                next;

            } elsif ($lo4 == 0x0E) {
                # VOL - volume
                $s->{vol} = $hi4;
                my $cc7 = int($s->{vol} * 127 / 15);
                vprintf("    [v%d pos=%04X]  VOL %d (CC7=%d)\n", $v, $s->{pos}, $s->{vol}, $cc7);
                for my $ch (0..2) {
                    push @{$e->[$v]}, ['control_change', $s->{totaltime}, $ch, 7, $cc7];
                }

            } elsif ($lo4 == 0x02) {
                # CAL 0-15
                handle_call($hi4, $s, $next_pos);
                next;

            } elsif ($opt == 0x0B || (($opt & 0x0F) == 0x0B && ($opt & 0x80))) {
                # BMP - bump volume up/down
                my $down = ($opt >> 2) & 0x01;
                if ($down) { $s->{vol}-- if $s->{vol} > 0; }
                else        { $s->{vol}++ if $s->{vol} < 15; }
                my $cc7 = int($s->{vol} * 127 / 15);
                vprintf("    [v%d pos=%04X]  BMP %s -> vol=%d (CC7=%d)\n",
                        $v, $s->{pos}, ($down ? "DN" : "UP"), $s->{vol}, $cc7);
                for my $ch (0..2) {
                    push @{$e->[$v]}, ['control_change', $s->{totaltime}, $ch, 7, $cc7];
                }

            } elsif ($lo4 == 0x06) {
                # DEF 0-15
                my $phrase = $hi4;
                $phrases[$phrase] = $next_pos;
                push @{$s->{defining}}, $phrase;
                vprintf("    [v%d pos=%04X]  DEF phrase=%d (body at %04X)\n",
                        $v, $s->{pos}, $phrase, $next_pos);

            } elsif (($opt & 0x8F) == 0x83) {
                # DEF 16-23: pattern 1nnn 0011
                my $phrase = ($opt >> 4) + 8;
                $phrases[$phrase] = $next_pos;
                push @{$s->{defining}}, $phrase;
                vprintf("    [v%d pos=%04X]  DEF phrase=%d (body at %04X)\n",
                        $v, $s->{pos}, $phrase, $next_pos);

            } elsif (($opt & 0x8F) == 0x8B) {
                # CAL 16-23: pattern 1nnn 1011
                my $phrase = ($opt >> 4) + 8;
                handle_call($phrase, $s, $next_pos);
                next;

            } elsif ($lo4 == 0x07) {
                my $wav = ($opt >> 5) & 0x07;
                my @wav_names = qw(Noise Triangle Sawtooth Tri+Saw Pulse Tri+Pulse Saw+Pulse Tri+Saw+Pulse);
                vprintf("    [v%d pos=%04X]  WAV %s\n", $v, $s->{pos}, $wav_names[$wav]);

            } elsif (($opt & 0x07) == 0x04 && !($opt & 0x80)) {
                # ATK: pattern 0nnn n100, value = bits 6-3
                vprintf("    [v%d pos=%04X]  ATK %d\n", $v, $s->{pos}, ($opt >> 3) & 0x0F);
            } elsif ($lo4 == 0x00 && $hi4 > 0) {
                # DCY: aaan0000, aaa != 0
                vprintf("    [v%d pos=%04X]  DCY %d\n", $v, $s->{pos}, $hi4);
            } elsif (($opt & 0x07) == 0x04 && ($opt & 0x80)) {
                # SUS: pattern 1nnn n100, value = bits 6-3
                vprintf("    [v%d pos=%04X]  SUS %d\n", $v, $s->{pos}, ($opt >> 3) & 0x0F);
            } elsif ($lo4 == 0x08) {
                if ($hi4 == 0) {
                    $s->{abs_pitch}    = undef;
                    $s->{abs_velocity} = undef;
                }
                vprintf("    [v%d pos=%04X]  REL %d\n", $v, $s->{pos}, $hi4);
            } elsif ($lo4 == 0x0A) {
                vprintf("    [v%d pos=%04X]  RES %d\n", $v, $s->{pos}, $hi4);
            } elsif ($opt == 0x13) { vprintf("    [v%d pos=%04X]  FLT NO\n",  $v, $s->{pos});
            } elsif ($opt == 0x1B) { vprintf("    [v%d pos=%04X]  FLT YES\n", $v, $s->{pos});
            } elsif ($opt == 0x23) { vprintf("    [v%d pos=%04X]  RNG OFF\n", $v, $s->{pos});
            } elsif ($opt == 0x2B) { vprintf("    [v%d pos=%04X]  RNG ON\n",  $v, $s->{pos});
            } elsif ($opt == 0x33) { vprintf("    [v%d pos=%04X]  SNC OFF\n", $v, $s->{pos});
            } elsif ($opt == 0x3B) { vprintf("    [v%d pos=%04X]  SNC ON\n",  $v, $s->{pos});
            } elsif ($opt == 0x43) { vprintf("    [v%d pos=%04X]  F-X NO\n",  $v, $s->{pos});
            } elsif ($opt == 0x4B) { vprintf("    [v%d pos=%04X]  F-X YES\n", $v, $s->{pos});
            } elsif ($opt == 0x5B) { vprintf("    [v%d pos=%04X]  3-O YES\n", $v, $s->{pos});
            } elsif ($opt == 0x63) { vprintf("    [v%d pos=%04X]  LFO tri\n", $v, $s->{pos});
            } elsif ($opt == 0x6B) { vprintf("    [v%d pos=%04X]  LFO pls\n", $v, $s->{pos});
            } elsif ($opt == 0x73) { vprintf("    [v%d pos=%04X]  P&V OFF\n", $v, $s->{pos});
            } elsif ($opt == 0x7B) { vprintf("    [v%d pos=%04X]  P&V ON\n",  $v, $s->{pos});
            } elsif (($opt & 0x07) == 0x01) {
                vprintf("    [v%d pos=%04X]  RUP %d\n", $v, $s->{pos}, ($opt >> 3) & 0x1F);
            } elsif (($opt & 0x07) == 0x05) {
                vprintf("    [v%d pos=%04X]  RDN %d\n", $v, $s->{pos}, ($opt >> 3) & 0x1F);
            } elsif (($opt & 0x1F) == 0x1F && !($opt & 0x80)) {
                vprintf("    [v%d pos=%04X]  SRC %d\n", $v, $s->{pos}, ($opt >> 5) & 0x03);
            } elsif (($opt & 0x0F) == 0x0F && ($opt & 0x80)) {
                vprintf("    [v%d pos=%04X]  DST %d\n", $v, $s->{pos}, ($opt >> 4) & 0x07);
            } else {
                vprintf("    [v%d pos=%04X]  UNKNOWN cmd=\$%02X opt=\$%02X\n",
                        $v, $s->{pos}, $cmd, $opt);
            }
            $s->{pos} = $next_pos;
            next;
        }

        # ---- bits 0-1 = 11: Portamento ----------------------------------
        if ($cmdtype == 3) {
            my $por_val = (($cmd >> 2) << 8) | $opt;
            vprintf("    [v%d pos=%04X]  POR %d\n", $v, $s->{pos}, $por_val);
            $s->{pos} = $next_pos;
            next;
        }

        # ---- bits 0-1 = 10: Register/parameter commands -----------------

        if ($cmd == 0x06) {
            # TEM - tempo
            my $T = $opt || 256;
            $shared->{tempo} = $T;
            my $usec = calc_tempo_usec($T, $shared->{jiffy_usec});
            vprintf("    [v%d pos=%04X]  TEM \$%02X (MM %d)\n", $v, $s->{pos}, $opt, int(14400/$T));
            push @$cevents, ['set_tempo', $s->{totaltime}, $usec];

        } elsif ($cmd == 0x16) {
            # UTL - utility duration (all voices)
            $shared->{utl_jiffies} = $opt;
            vprintf("    [v%d pos=%04X]  UTL %d jiffies\n", $v, $s->{pos}, $opt);

        } elsif ($cmd == 0xF6) {
            # UTV - utility duration (this voice)
            $s->{utv_jiffies} = $opt;
            vprintf("    [v%d pos=%04X]  UTV %d jiffies\n", $v, $s->{pos}, $opt);

        } elsif ($cmd == 0x36) {
            # HED - repeat head
            my $count = $opt;
            if ($count == 0) {
                warn sprintf("  WARNING: Voice %d infinite repeat (HED 0) at pos %04X, capping at 2\n",
                             $v, $s->{pos});
                $count = 2;
            }
            $s->{repeat_pos}   = $next_pos;
            $s->{repeat_count} = $count;
            vprintf("    [v%d pos=%04X]  HED count=%d t=%d\n", $v, $s->{pos}, $count, $s->{totaltime});

        } elsif ($cmd == 0xA6) {
            # TPS - transpose
            $s->{transpose} = decode_tps($opt);
            vprintf("    [v%d pos=%04X]  TPS \$%02X -> %+d semitones\n",
                    $v, $s->{pos}, $opt, $s->{transpose});

        } elsif ($cmd == 0x2E) {
            # RTP - relative transpose
            my $rtp = decode_rtp($opt);
            $s->{transpose} += $rtp;
            vprintf("    [v%d pos=%04X]  RTP \$%02X -> %+d (total %+d)\n",
                    $v, $s->{pos}, $opt, $rtp, $s->{transpose});

        } elsif (($cmd & 0x3F) == 0x1E) {
            # MS# - measure number
            my $msnum = (($cmd >> 6) << 8) | $opt;
            vprintf("    [v%d pos=%04X]  MS# %d\n", $v, $s->{pos}, $msnum);

        } elsif (($cmd & 0x3F) == 0x3E) {
            # JIF - jiffy clock adjustment
            my $jif_hi  = $opt;
            my $jif_lo  = ($cmd >> 6) & 0x03;
            my $jif_val = ($jif_hi << 2) | $jif_lo;
            $jif_val -= 1024 if $jif_val >= 512;  # sign-extend from 10 bits
            my $new_timer = $CIA_BASE_NTSC + $jif_val;
            $shared->{jiffy_usec} = $new_timer * 1_000_000 / $CLOCK_NTSC;
            my $usec = calc_tempo_usec($shared->{tempo}, $shared->{jiffy_usec});
            vprintf("    [v%d pos=%04X]  JIF %d (timer=\$%04X, jiffy=%.1f usec)\n",
                    $v, $s->{pos}, $jif_val, $new_timer, $shared->{jiffy_usec});
            push @$cevents, ['set_tempo', $s->{totaltime}, $usec];

        } elsif ($cmd == 0x26) {
            $s->{pnt_jiffies} = $opt;
            vprintf("    [v%d pos=%04X]  PNT %d jiffies\n", $v, $s->{pos}, $opt);
        } elsif ($cmd == 0x4E) {
            $s->{hld_jiffies} = $opt;
            vprintf("    [v%d pos=%04X]  HLD %d jiffies\n", $v, $s->{pos}, $opt);
        } elsif ($cmd == 0x46) {
            vprintf("    [v%d pos=%04X]  FLG \$%02X\n", $v, $s->{pos}, $opt);
        } elsif ($cmd == 0xB6) {
            vprintf("    [v%d pos=%04X]  AUX \$%02X\n", $v, $s->{pos}, $opt);
        } elsif ($cmd == 0x56) {
            vprintf("    [v%d pos=%04X]  P-S %d\n", $v, $s->{pos}, unpack('c', pack('C', $opt)));
        } elsif ($cmd == 0x66) {
            vprintf("    [v%d pos=%04X]  F-S %d\n", $v, $s->{pos}, unpack('c', pack('C', $opt)));
        } elsif ($cmd == 0x6E) {
            vprintf("    [v%d pos=%04X]  SCA %d\n", $v, $s->{pos}, unpack('c', pack('C', $opt)));
        } elsif ($cmd == 0x76) {
            vprintf("    [v%d pos=%04X]  VDP %d\n", $v, $s->{pos}, $opt & 0x7F);
        } elsif ($cmd == 0x86) {
            vprintf("    [v%d pos=%04X]  VRT %d\n", $v, $s->{pos}, $opt);
        } elsif ($cmd == 0x96) {
            vprintf("    [v%d pos=%04X]  AUT %d\n", $v, $s->{pos}, unpack('c', pack('C', $opt)));
        } elsif ($cmd == 0xC6) {
            vprintf("    [v%d pos=%04X]  PVD %d\n", $v, $s->{pos}, $opt);
        } elsif ($cmd == 0xD6) {
            vprintf("    [v%d pos=%04X]  PVR %d\n", $v, $s->{pos}, $opt & 0x7F);
        } elsif ($cmd == 0xE6) {
            vprintf("    [v%d pos=%04X]  MAX %d\n", $v, $s->{pos}, $opt);
        } elsif ($cmd == 0x0E) {
            vprintf("    [v%d pos=%04X]  F-C %d\n", $v, $s->{pos}, $opt);
        } elsif (($cmd & 0x0F) == 0x02) {
            # P-W: cmd bits 7-4 = hi nibble, opt = lo byte
            vprintf("    [v%d pos=%04X]  P-W %d\n", $v, $s->{pos}, (($cmd >> 4) << 8) | $opt);
        } elsif (($cmd & 0x0F) == 0x0A) {
            # DTN
            my $dtn = (($cmd >> 4) << 8) | $opt;
            $dtn -= 2048 if $cmd & 0x10;
            vprintf("    [v%d pos=%04X]  DTN %d\n", $v, $s->{pos}, $dtn);
        } else {
            vprintf("    [v%d pos=%04X]  UNKNOWN cmd=\$%02X opt=\$%02X\n",
                    $v, $s->{pos}, $cmd, $opt);
        }

        $s->{pos} = $next_pos;
    }

    # Fell off the end of voice data without HLT
    $s->{done} = 1;
}

# -------------------------------------------------------------------------
# Helper: handle CALL (shared by both CAL encodings)
# -------------------------------------------------------------------------

sub handle_call {
    my ($phrase, $s, $next_pos) = @_;
    my $v = $s->{v};

    if (!defined $phrases[$phrase]) {
        warn sprintf("  WARNING: Voice %d CALL undefined phrase %d at pos %04X\n",
                     $v, $phrase, $s->{pos});
        $s->{pos} = $next_pos;
        return;
    }
    if (@{$s->{call_stack}} >= $MAX_CALL_DEPTH) {
        warn sprintf("  WARNING: Voice %d CALL stack overflow (depth %d) at pos %04X\n",
                     $v, scalar(@{$s->{call_stack}}), $s->{pos});
        $s->{pos} = $next_pos;
        return;
    }
    vprintf("    [v%d pos=%04X]  CALL phrase=%d (jump to %04X, return to %04X)\n",
            $v, $s->{pos}, $phrase, $phrases[$phrase], $next_pos);
    push @{$s->{call_stack}}, $next_pos;
    $s->{pos} = $phrases[$phrase];
}

# -------------------------------------------------------------------------
# Timing calculations
# -------------------------------------------------------------------------

sub calc_tempo_usec {
    my ($T, $usec_per_jiffy) = @_;
    $usec_per_jiffy //= 1_000_000 / 60;
    # quarter note = T/4 jiffies
    return int($T * $usec_per_jiffy / 4 + 0.5);
}

sub jiffies_to_ticks {
    my ($jiffies, $tempo, $usec_per_jiffy) = @_;
    $usec_per_jiffy //= 1_000_000 / 60;
    # quarter note = tempo/4 jiffies = TICKS_PER_QUARTER ticks
    return int($jiffies * 4 * $TICKS_PER_QUARTER / $tempo + 0.5);
}

# -------------------------------------------------------------------------
# Note pitch calculation
# -------------------------------------------------------------------------

sub note_to_midi {
    my ($note_idx, $accidental, $octave, $transpose) = @_;
    my $semitone   = $NOTE_SEMITONES[$note_idx];
    my $acc_offset = ($accidental == 0x01) ? +1    # sharp
                   : ($accidental == 0x03) ? -1    # flat
                   : ($accidental == 0x00)          # double: sharp for GFDC, flat for ABE
                     ? (($note_idx == 5 || $note_idx == 4 || $note_idx == 2 || $note_idx == 1) ? +2 : -2)
                   :                          0;   # natural (0x02)
    my $midi = 12 * ($octave + 1) + $semitone + $acc_offset + $transpose;
    $midi = 0   if $midi < 0;
    $midi = 127 if $midi > 127;
    return $midi;
}

sub note_name {
    my ($note_idx, $accidental, $octave, $transpose) = @_;
    my @names = qw(Rest C D E F G A B);
    my $name = $names[$note_idx];
    $name .= '#' if $accidental == 0x01;
    $name .= 'b' if $accidental == 0x03;
    $name .= 'x' if $accidental == 0x00 && $note_idx != 0;
    $name .= $octave if $note_idx != 0;
    $name .= sprintf("(%+d)", $transpose) if $transpose && $note_idx != 0;
    return $name;
}

# -------------------------------------------------------------------------
# TPS decode (from modland doc)
# Bit 0 = sign: 0=positive, 1=negative
# Positive: octaves = 7 - bits[3:1], half-steps = bits[7:4]
# Negative: octaves = bits[3:1],     half-steps = 11 - bits[7:4]
# -------------------------------------------------------------------------

sub decode_tps {
    my ($byte) = @_;
    my $negative   = $byte & 0x01;
    my $octs_field = ($byte >> 1) & 0x07;
    my $semi_field = ($byte >> 4) & 0x0F;
    if ($negative) {
        return -(($octs_field * 12) + (11 - $semi_field));
    } else {
        return ((7 - $octs_field) * 12) + $semi_field;
    }
}

# -------------------------------------------------------------------------
# RTP decode (from modland doc)
# Bits 2-0: octaves = 3 - value
# Bits 7-3: half-steps = value - 11
# -------------------------------------------------------------------------

sub decode_rtp {
    my ($byte) = @_;
    my $octs_field = $byte & 0x07;
    my $semi_field = ($byte >> 3) & 0x1F;
    return (3 - $octs_field) * 12 + ($semi_field - 11);
}

# -------------------------------------------------------------------------
# Verbose print
# -------------------------------------------------------------------------

sub vprintf {
    return if $quiet;
    if ($disasm && $_[0] =~ /\[v%d pos=%04X\]/) {
        my ($fmt, @args) = @_;
        $fmt =~ s/(\[v%d pos=%04X\]\s+)/$1$disasm_bytes/;
        printf STDERR $fmt, @args;
    } else {
        printf STDERR @_;
    }
}
