#!/usr/bin/perl

# 6 Apr 2001  cab@tc.umn.edu

use MIDI;
use Data::Dumper;

open ROM, "< seiken3e.smc"
  or die "open failed: $!\n";

$base = s2o('C5/0000');
$spcbase = $base + 0x8bd + 2 - 0x200;

@songtitle = ("00", "Whiz Kid", "Left-Handed Wolf", "Raven", "Witchmakers",
              "Evening Star", "Female Turbulence", "Three of Darkside",
              "Ordinary People", "Ancient Dolphin", "Political Pressure",
              "Few Paths Forbidden", "Little Sweet Cafe", "Swivel",
              "Harvest November", "Walls and Steels", "Powell",
              "Damn Damn Drum", "Don't Hunt the Fairy", "Another Winter",
              "Different Road", "Weird Counterpoint", "Electric Talk",
              "Hope Isolation Pray", "Intolerance", "Fable", "Innocent Sea",
              "Delicate Affection", "Meridian Child", "Nuclear Fusion",
              "Sacrifice, Part 1", "Strange Medicine", "Obsession",
              "Rolling Cradle", "High Tension Wire", "Faith Total Machine",
              "Secret of Mana", "Black Soup", "Frenzy", "Innocent Water",
              "Can You Fly, Sister", "Splash Hop", "Closed Garden",
              "Where Angels Fear to Tread", "Farewell Song", "Positive",
              "And Other", "Long Goodbye", "Axe Bring Storm",
              "Last Audience", "(fx) Wind", "Oh I'm a Flamelet",
              "(fx) Heartbeat", "(fx) Ghost Ship", "(fx) Drumroll",
              "(fx) Cannon", "(fx) Flammie", "Person's Die",
              "Angel's Fear", "Religion Thunder", "Sacrifice, Part 2",
              "(fx) Sleeping", "3e", "Legend", "Decision Bell",
              "(fx) Level Up", "Not Awaken", "Sacrifice, Part 3",
              "(fx) Pihyara Flute", "(fx) Wind Drum", "Breezin",
              "Return to Forever", "48", "(fx) Gate", "(fx) Mana Seed",
              "Reincarnation");

%musicop = (0xc4 => "Inc Octave", 0xc5 => "Dec Octave", 0xc6 => "Set Octave",
            0xc7 => "nop", 0xc8 => "Set Noise Freq", 0xc9 => "Noise on",
            0xca => "Noise off", 0xcb => "Pitchmod on",
            0xcc => "Pitchmod off", 0xcd => "JMP SFX (lo)",
            0xce => "JMP SFX (hi)", 0xcf => "Detune (/16)",
	    0xd0 => "End/Return", 0xd1 => "Tempo", 0xd2 => "Begin Repeat",
            0xd3 => "Begin Repeat", 0xd4 => "Begin Repeat",
            0xd5 => "End Repeat", 0xd6 => "Alternate Repeat",
            0xd7 => "Mark Return Point", 0xd8 => "Default ADSR",
	    0xd9 => "ADSR Attack", 0xda => "ADSR Decay",
	    0xdb => "ADSR Sustain", 0xdc => "ADSR Release",
            0xdd => "Note Length %", 0xde => "Patch Change",
            0xdf => "Change Noise Freq", 0xe0 => "Set Voice Volume",
            0xe1 => "Unused", 0xe2 => "Set Voice Volume",
            0xe3 => "Change Voice Volume", 0xe4 => "Voice Volume Fade",
            0xe5 => "Portamento", 0xe6 => "Portamento Toggle", 0xe7 => "Pan",
            0xe8 => "Pan Fade", 0xe9 => "Ping-pong on", 
            0xea => "Restart Ping-pong", 0xeb => "Ping-pong off",
            0xec => "Set Detune (/4)", 0xed => "Change Detune (/4)",
            0xee => "Percussion Mode on", 0xef => "Percussion Mode off",
            0xf0 => "Vibrato on", 0xf1 => "Vibrato on (w/delay)",
	    0xf2 => "Change Tempo", 0xf3 => "Vibrato off",
	    0xf4 => "Tremolo on", 0xf5 => "Tremolo on (w/delay)",
	    0xf6 => "Inc Octave", 0xf7 => "Tremolo off", 0xf8 => "Slur on",
            0xf9 => "Slur off", 0xfa => "Echo on", 0xfb => "Echo off",
            0xfc => "Call SFX (lo)", 0xfd => "Call SFX (hi)",
            0xfe => "Inc Octave", 0xff => "Inc Octave");

@notes = ("C ", "C#", "D ", "D#", "E ", "F ",
          "F#", "G ", "G#", "A ", "A#", "B ");

seek ROM, $base + 0x2064, 0;
read ROM, $buf, 1;
$numsongs = ord $buf;

if (@ARGV) {
  foreach (@ARGV) {
    $dosong[hex $_] = 1;
  }
} else {
  @dosong = (1) x $numsongs;
}  

@songptrs = &read3ptrs($base + 0x2065, $numsongs);

# get SPC data - music ops lengths, pitch table, duration table
seek ROM, $spcbase + 0x1765, 0;
read ROM, $buf, 60;
@musoplen = unpack "C60", $buf;

seek ROM, $spcbase + 0x17af, 0;
read ROM, $buf, 26;
@pitchtbl = unpack "v13", $buf;

seek ROM, $spcbase + 0x17a1, 0;
read ROM, $buf, 14;
@durtbl = unpack "C14", $buf;
#print STDERR "len:\n", join("\n", map {sprintf "%02x:%02x", ($loop++) + 0xc4, $_} @musoplen), "\n";
#print STDERR "dur: ", join(",", map {sprintf "%02x", $_} @durtbl), "\n";

#$data40_len = &read2ptrs(&s2o('C5/205C'), 1);
seek ROM, $base + 0x2149, 0;
read ROM, $data40, 0x1e00;
open OUT, "> txt/data40" or warn;
for ($p=0; $p < 0x400; $p += 4) {
  my ($e9addr, $eaaddr) = unpack "v2", substr($data40, $p, 4);
  printf OUT "%02x: %04x,%04x\n", $p/4, $e9addr, $eaaddr;
}
print OUT "\n";
for ($p = 0x400; $p < 0x1e00; $p++) {
      printf OUT "      %04X: ", $p + 0x4000;
      my $cmd = ord(substr($data40, $p, 1));
      printf OUT "%02X", $cmd;
      if ($cmd < 0xc4) {
        # note
        my ($dur, $note) = (int($cmd/14), $cmd % 14);
        if ($dur == 13) {
          $dur = ord(substr($data40, ++$p, 1));
          printf OUT " %02X", $dur;
        } else {
          $dur = $durtbl[$dur];
          print OUT "   ";
        }
        $dur++;
        print OUT "   " x 3;  # pad out
        if ($note < 12) {
          printf OUT "Note %s (%02d) Dur %02X\n", $notes[$note], $note, $dur;
        } elsif ($note == 13) {
          printf OUT "Tie          Dur %02X\n", $dur;
        } else {
          printf OUT "Rest         Dur %02X\n", $dur;
        }
      } else {
        # command
        my $oplen = $musoplen[$cmd - 0xc4];
        $oplen = 1 if $oplen == 0xff || $oplen == 0;
        $oplen--;

        print OUT map {sprintf " %02X",$_}
                unpack("C$oplen", substr($data40, $p + 1, $oplen)) if $oplen;
        print OUT "   " x (4-$oplen);  # pad out
        print OUT "$musicop{$cmd}";
        print OUT "\n" if $cmd == 0xd0;
        $p += $oplen;  # loop takes care of cmd
        print OUT "\n";
      }
}
close OUT;


for ($i = 0, $numdone=0; $i <= $numsongs; $i++) {
  next unless $dosong[$i];
  my ($opus);

  printf STDERR "%02X ", $i;
  print STDERR "\n" if ++$numdone % 16 == 0;

  open OUT, sprintf "> txt/%02X - %s.txt", $i, $songtitle[$i];

  # grab instrument table
  seek ROM, $songptrs[$i], 0;
  read ROM, $buf, 0x43;  # catch up to $20 samples plus FF terminator & size
  my (@inst, @instvol, $len);
  for ($p = 0; $p < length $buf; $p+=2) {
    my ($inst, $instvol) = unpack "C2", substr($buf, $p, 2);
    if ($inst == 0xff) {
      $len = unpack "v", substr($buf, $p + 1, 2);
      last;
    }
    push @inst, $inst;
    push @instvol, $instvol;
  }

  seek ROM, $songptrs[$i] + @inst * 2 + 3, 0;
  # read all song data
  read ROM, $song, $len;

  my (@vstart) = unpack "v8", substr($song, 0, 0x10);
  my (%percinst);
  for ($p = 0x10; $p < $len; $p += 5) {
    my ($key, $instr, $note, $vol, $pan) = unpack "C5", substr($song, $p, 5);
    last if $key == 0xff;
    $percinst{$key} = { 'instr' => $instr, 'note' => $note, 'vol' => $vol,
                        'pan' => $pan };
  }

  printf OUT "Song %02X - %s:\n", $i, $songtitle[$i];
  printf OUT "  Start %s  Length %04X\n", o2s($songptrs[$i]), $len;
  print OUT  "  Instruments:";
  for ($j = 0; $j < @inst; $j++) {
    printf OUT " %02X", $inst[$j];
  }
  print OUT  "\n   Instr Vols:";
  for ($j = 0; $j < @instvol; $j++) {
    printf OUT " %02X", $instvol[$j];
  }
  if (%percinst) {
    print OUT "\n   Percussion:\n";
    foreach $j (sort {$a <=> $b} keys %percinst) {
      printf OUT "      %02X:  instr %02X note %02X vol %02X pan %02X\n",
        $j, @{$percinst{$j}}{instr, note, vol, pan};
    }
  }
  print OUT "\n  Voice start addresses:\n   ";
  for ($j = 0; $j < 8; $j++) {
    printf OUT " %d:%04X", $j, $vstart[$j];
  }
  print OUT "\n";

  for ($v = 0; $v < 8; $v++) {
    next if $vstart[$v] < 0x100;

    print OUT "\n" if $v > 0;
    print OUT "    Voice $v data:\n";

    for ($p = $vstart[$v] - 0x2000; $p < $len; $p++) {
      last if grep {$_ == $p + 0x2000 && $_ ne $vstart[$v]} @vstart;

      printf OUT "      %04X: ", $p + 0x2000;
      my $cmd = ord(substr($song, $p, 1));
      printf OUT "%02X", $cmd;
      if ($cmd < 0xc4) {
        # note
        my ($dur, $note) = (int($cmd/14), $cmd % 14);
        if ($dur == 13) {
          $dur = ord(substr($song, ++$p, 1));
          printf OUT " %02X", $dur;
        } else {
          $dur = $durtbl[$dur];
          print OUT "   ";
        }
        $dur++;
        print OUT "   " x 3;  # pad out
        if ($note < 12) {
          printf OUT "Note %s (%02d) Dur %02X\n", $notes[$note], $note, $dur;
        } elsif ($note == 13) {
          printf OUT "Tie          Dur %02X\n", $dur;
        } else {
          printf OUT "Rest         Dur %02X\n", $dur;
        }
      } else {
        # command
        my $oplen = $musoplen[$cmd - 0xc4];
        $oplen = 1 if $oplen == 0xff || $oplen == 0;
        $oplen--;

        print OUT map {sprintf " %02X",$_}
                unpack("C$oplen", substr($song, $p + 1, $oplen)) if $oplen;
        print OUT "   " x (4-$oplen);  # pad out
        print OUT "$musicop{$cmd}";

	my ($op1, $op2, $op3, $op4) = unpack("C4", substr($song, $p+1, 4));

        #if ($cmd == 0xf6 || $cmd == 0xfc) {
        #  my $newp = (($op1 + $op2 * 256) + $vaddroffset) & 0xffff;
        #  printf OUT ": %04X", $newp;
        #} elsif ($cmd == 0xf5) {
        #  my $newp = (($op2 + $op3 * 256) + $vaddroffset) & 0xffff;
        #  printf OUT ": %04X at %02X", $newp, $op1;
        #} elsif ($cmd == 0xc5 || $cmd == 0xc7 || $cmd == 0xf1 ||
        #         $cmd == 0xf3 || $cmd == 0xf7 || $cmd == 0xf8) {
        #  # fades
        #  printf OUT ": steps=%02X value=%02X", $op1, $op2;
        #} elsif ($cmd == 0xc8) {
        #  printf OUT ": steps=%02X halfsteps=%02X", $op1, $op2;
        #} elsif ($cmd == 0xc9 || $cmd == 0xcb) {
        #  printf OUT ": delay=%02X steps=%02X depth=%02X", $op1, $op2, $op3;
        #} elsif ($cmd == 0xcd) {
        #  printf OUT ": steps=%02X direction=%02X", $op2, $op1;
        #} elsif ($cmd == 0xcf) {
        #  my (@nclk) = qw(0 16 21 25 31 42 50 63 84 100 125 167
        #                  200 250 333 400 500 667 800 1000 1300
        #                  1600 2000 2700 3200 4000 5300 6400 8000
        #                  10700 16000 32000);
        #  printf OUT ": freq=%s Hz", $nclk[$op1 & 0x1F];
        #} elsif ($oplen == 1) {
        #  $op1++ if $cmd == 0xe2 && $op1 > 0;  # rpt begin
        #  printf OUT ": %02X", $op1;
        #} 

        $p += $oplen;  # loop takes care of cmd
        print OUT "\n";
      }
    }
  }
  close OUT;
}

close ROM;
print STDERR "\n";
## the end


sub read2ptrs {
  # read $count 2-byte little-endian pointers from offset $start
  my ($start, $count) = @_;
  my ($buf);
  seek ROM, $start, 0;
  read ROM, $buf, $count * 2;
  return unpack "v$count", $buf;
}

sub read3ptrs {
  # read $count 3-byte little-endian pointers from offset $start
  my ($start, $count) = @_;
  my ($buf);
  seek ROM, $start, 0;
  read ROM, $buf, $count * 3;
  return map { &ptr2sv($_) } unpack ("a3" x $count, $buf); 
}

sub ptr2sv {
  my ($a,$b,$c) = map { ord } split //, $_[0];
  return $a + 0x100 * $b + 0x10000 * ($c & 0x3f) + 0x200;
}

sub s2o {
  # hirom
  my ($snes) = @_;
  $snes =~ s/^(..)\/?(....)$/$1$2/;   # trim bank separator if need be

  my ($bank, $offset) = map { hex } (unpack "A2A4", $snes);
  return ($bank & 0x3f) * 0x10000 + $offset + 0x200; 
}

sub o2s {
  my ($offs) = @_;

  my ($bank, $offset);
  $offs -= 0x200;  # trim smc header
  $bank = int($offs / 0x10000) | 0xc0;
  $offset = $offs & 0xFFFF;
  return sprintf "%02X/%04X", $bank, $offset;
}

