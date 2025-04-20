#!/usr/bin/perl

# 25 Feb 2001  cab@tc.umn.edu

use MIDI;
use Data::Dumper;

open ROM, "< chrono.smc"
  or die "open failed: $!\n";

$base = s2o('C7/0000');
$spcbase = $base + 0x24c3 + 2 - 0x200;

@songtitle = ("00", "Memories of Green", "Wind Scene", "Corridors of Time",
              "Rhythm of Wind, Sky, and Earth", "Ruined World",
              "Guardia Millenial Fair", "Far Off Promise",
              "Secret of the Forest", "Zeal Palace", "Remains of Factory",
              "Ayla's Theme", "Courage and Pride", "Lavos' Theme",
              "Robo's Theme", "Morning Sunlight", "Manoria Cathedral",
              "11", "FX-Leene's Bell", "Wings That Cross Time",
              "Schala's Theme", "Delightful Spekkio", "A Shot of Crisis",
              "Kingdom Trial", "Chrono Trigger (a)", "FX-19", "FX-1a",
              "Fanfare 1", "Fanfare 2", "At the Bottom of Night",
              "Peaceful Day", "A Strange Happening", "FX-Drips",
              "FX-21", "FX-22", "The Hidden Truth",
              "A Prayer to the Road That Leads", "Huh",
              "The Day the World Revived", "Robo Gang Johnny",
              "Battle with Magus", "Boss Battle 1", "Kaeru's Theme",
              "Goodnight", "Bike Chase",
              "People Who Threw Away the Will to Live",
              "Mystery of the Past", "Underground Sewer", "Presentiment",
              "Undersea Palace", "Last Battle", "Dome 16's Ruin",
              "FX-Heartbeat (Slow)", "FX-35", "Burn Bobonga", "FX-37",
              "Primitive Mountain", "World Revolution", "FX-3a",
              "Sealed Door", "Silent Light", "Fanfare 3",
              "The Brink of Time", "To Far Away Times",
              "Confusing Melody", "FX-41", "Gonzalez' Song", "FX-43",
              "Black Dream", "Battle 1", "Tyran Castle", "FX-47",
              "Magus' Castle", "First Festival of Stars", "FX-4a", "FX-4b",
              "FX-4c", "Epilogue - To Good Friends", "Boss Battle 2",
              "Lose", "Determination", "Battle 2", "Singing Mountain");

%musicop = (0xc4 => "Set Volume", 0xc5 => "Volume Fade",
            0xc6 => "Set Pan", 0xc7 => "Pan Fade",
            0xc8 => "Portamento?", 0xc9 => "Vibrato", 0xca => "Vibrato Off",
            0xcb => "Tremolo", 0xcc => "Tremolo Off", 0xcd => "Pan Sweep",
            0xce => "Pan Sweep Off", 0xcf => "Set Noise Clock",
	    0xd0 => "Enable Noise", 0xd1 => "Disable Noise",
	    0xd2 => "Enable Pitchmod", 0xd3 => "Disable Pitchmod",
	    0xd4 => "Enable Echo", 0xd5 => "Disable Echo",
	    0xd6 => "Set Octave", 0xd7 => "Inc Octave", 0xd8 => "Dec Octave",
	    0xd9 => "Set Transpose?", 0xda => "Change Transpose?",
	    0xdb => "Detune", 0xdc => "Patch Change",
            0xdd => "Set ADSR Attack", 0xde => "Set ADSR Decay",
            0xdf => "Set ADSR Sustain", 0xe0 => "Set ADSR Release",
            0xe1 => "Set Default ADSR", 0xe2 => "Begin Repeat",
            0xe3 => "End Repeat", 0xe4 => "Begin Slur?", 0xe5 => "End Slur?",
            0xe6 => "Begin Roll?", 0xe7 => "End Roll?", 0xe8 => "Utility Dur",
	    0xe9 => "Play SFX (E9)", 0xea => "Play SFX (EA)",
            0xeb => "Halt", 0xec => "Halt", 0xed => "Halt", 0xee => "Halt",
	    0xef => "Halt", 0xf0 => "Set Tempo", 0xf1 => "Tempo Fade",
	    0xf2 => "Set Echo Volume", 0xf3 => "Fade Echo Volume",
	    0xf4 => "Master Volume?", 0xf5 => "Conditional Loop",
	    0xf6 => "Goto", 0xf7 => "Set/Fade Echo Feedback",
	    0xf8 => "Set/Fade Filter", 0xf9 => "f9",
	    0xfa => "fa", 0xfb => "Percussion Mode on",
            0xfc => "Peercussion Mode off", 0xfd => "fd", 0xfe => "Halt",
            0xff => "Halt");

@notes = ("C ", "C#", "D ", "D#", "E ", "F ",
          "F#", "G ", "G#", "A ", "A#", "B ");

seek ROM, $base + 0x0ae9, 0;
read ROM, $buf, 1;
$numsongs = ord $buf;

if (@ARGV) {
  foreach (@ARGV) {
    $dosong[hex $_] = 1;
  }
} else {
  @dosong = (1) x $numsongs;
}

@songptrs = &read3ptrs($base + 0x0d18, $numsongs);

# get SPC data - music ops lengths, pitch table, duration table
seek ROM, $spcbase + 0x15cc, 0;
read ROM, $buf, 60;
@musoplen = unpack "C60", $buf;

seek ROM, $spcbase + 0x1d6d, 0;
read ROM, $buf, 26;
@pitchtbl = unpack "v13", $buf;

seek ROM, $spcbase + 0x1db3, 0;
read ROM, $buf, 14;
@durtbl = unpack "C14", $buf;
#print STDERR "len:\n", join("\n", map {sprintf "%02x:%02x", ($loop++) + 0xc4, $_} @musoplen), "\n";
#print STDERR "dur: ", join(",", map {sprintf "%02x", $_} @durtbl), "\n";

$data2c_len = &read2ptrs($base + 0x4309, 1);
read ROM, $data2c, $data2c_len;
open OUT, "> txt/data2F" or warn;
for ($p=0; $p < 0x400; $p += 4) {
  my ($e9addr, $eaaddr) = unpack "v2", substr($data2c, $p, 4);
  printf OUT "E9 %02x: %04x    EA %02x: %04x\n",
    $p/4, $e9addr, $p/4, $eaaddr;
}
print OUT "\n";
for ($p = 0x400; $p < $data2c_len; $p++) {
 last;
      printf OUT "      %04X: ", $p + 0x2f00;
      my $cmd = ord(substr($data2c, $p, 1));
      printf OUT "%02X", $cmd;
      if ($cmd < 0xc4) {
        # note
        print OUT "   " x 4;  # pad out
        my ($note, $dur) = (int($cmd/14), $cmd % 14);
        if ($note < 12) {
          printf OUT "Note %s (%02d) Dur %02X\n",
            $notes[$note], $note, $durtbl[$dur];

        } elsif ($note == 12) {
          printf OUT "Tie          Dur %02X\n", $durtbl[$dur];
        } else {
          printf OUT "Rest         Dur %02X\n", $durtbl[$dur];
        }
      } else {
        # command
        my $oplen = $musoplen[$cmd - 0xc4];
        print OUT map {sprintf " %02X",$_}
                unpack("C$oplen", substr($data2c, $p + 1, $oplen)) if $oplen;
        print OUT "   " x (4-$oplen);  # pad out
        print OUT "$musicop{$cmd}\n";
	print OUT "\n" if $cmd == 0xEB;
        $p += $oplen;  # loop takes care of cmd
      }
}
close OUT;


for ($i = 0, $numdone=0; $i <= $numsongs; $i++) {
  next unless $dosong[$i];
  my ($opus);

  printf STDERR "%02X ", $i;
  print STDERR "\n" if ++$numdone % 16 == 0;

  open OUT, sprintf "> txt/%02X - %s.txt", $i, $songtitle[$i];

  # do song i
  my ($len) = &read2ptrs($songptrs[$i], 1);
  # read all song data
  read ROM, $song, $len;
  # grab instrument table
  my (@inst) = map { $_ == 0 ? () : ($_) }
                 &read2ptrs($base + 0x0e11 + $i * 0x20, 0x10);

  # perc mode maps
  seek ROM, $base + 0x1871 + $i * 0x24, 0;
  read ROM, $buf, 0x24;
  my (@percinst);
  for ($p = 0; $p < 0x24; $p += 3) {
    my ($inst, $note, $vol) = unpack "C3", substr($buf, $p, 3);
    push @percinst, { 'instr' => $inst, 'note' => $note, 'vol' => $vol};
  }

  my ($vaddroffset, $emptyvoice)  =  unpack "v2", $song;
#printf STDERR "voff=%04x, ev=%04x\n", $vaddroffset, $emptyvoice;
  $vaddroffset = (0x12024 - $vaddroffset) & 0xffff;
#printf STDERR "voff2=%04x\n", $vaddroffset;
  my (@vstart) = unpack "v8", substr($song, 4, 0x10);
  my (@vstart2) = unpack "v8", substr($song, 0x14, 0x10);
#print STDERR "vs1: ", join(",", map {sprintf "%04x", $_} @vstart), "\n"; 
#print STDERR "vs2: ", join(",", map {sprintf "%04x", $_} @vstart2), "\n"; 
  @vstart = map {$_ == $emptyvoice ? 0 : ($_ + $vaddroffset) & 0xffff} @vstart;
  @vstart2 = map {$_ == $emptyvoice ? 0 : ($_ + $vaddroffset) & 0xffff} @vstart2;
#print STDERR "vs1b: ", join(",", map {sprintf "%04x", $_} @vstart), "\n"; 
#print STDERR "vs2b: ", join(",", map {sprintf "%04x", $_} @vstart2), "\n"; 

  printf OUT "Song %02X - %s:\n", $i, $songtitle[$i];
  printf OUT "  Start %s  Length %04X\n", o2s($songptrs[$i]), $len;
  print OUT  "  Instruments:";
  for ($j = 0; $j < @inst; $j++) {
    printf OUT " %02X", $inst[$j];
  }
  print OUT "\n  Percussion:\n";
  for ($j = 0; $j < @percinst; $j++) {
    next unless $percinst[$j]{'instr'};
    printf OUT "    %02x: instr %02x, note %02x, vol %02x\n",
      $j, $percinst[$j]{'instr'}, $percinst[$j]{'note'}, $percinst[$j]{'vol'};
  }
  print OUT "\n  Voice start addresses:\n   ";
  for ($j = 0; $j < 8; $j++) {
    printf OUT " %d:%04X", $j, $vstart[$j];
  }
  print OUT "\n  Voice alternate start addresses:\n   ";
  for ($j = 0; $j < 8; $j++) {
    printf OUT " %d:%04X", $j, $vstart2[$j];
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
        print OUT "   " x 4;  # pad out
        my ($note, $dur) = (int($cmd/14), $cmd % 14);
        if ($note < 12) {
          printf OUT "Note %s (%02d) Dur %02X\n",
            $notes[$note], $note, $durtbl[$dur];
        } elsif ($note == 12) {
          printf OUT "Tie          Dur %02X\n", $durtbl[$dur];
        } else {
          printf OUT "Rest         Dur %02X\n", $durtbl[$dur];
        }
      } else {
        # command
        my $oplen = $musoplen[$cmd - 0xc4];
        print OUT map {sprintf " %02X",$_}
                unpack("C$oplen", substr($song, $p + 1, $oplen)) if $oplen;
        print OUT "   " x (4-$oplen);  # pad out
        print OUT "$musicop{$cmd}";

	my ($op1, $op2, $op3) = map {ord} split //, substr($song, $p+1, 3);

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

