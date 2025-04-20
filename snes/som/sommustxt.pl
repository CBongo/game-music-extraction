#!/usr/bin/perl

# 27 Mar 2001  cab@tc.umn.edu

use MIDI;
use Data::Dumper;

open ROM, "< som1.smc"
  or die "open failed: $!\n";

$base = s2o('C3/0000');
$spcbase = $base + 0x748 + 2 - 0x200;

$tempo_factor = 56_075_000;  # 60M is standard MIDI divisor

@songtitle = ("00", "01", "02", "03", "04", "05", "06",
              "07", "08", "09", "0a", "0b", "0c", "0d", 
              "0e", "0f", "10", "11", "12", "13", "14",
              "15", "16", "17", "18", "19", "1a", "1b",
              "1c", "1d", "1e", "1f", "20", "21", "22",
              "23", "24", "25", "26", "27", "28", "29",
              "2a", "2b", "2c", "2d", "2e", "2f", "30",
              "31", "32", "33", "34", "35", "36", "37",
              "38", "39", "3a", "3b", "3c", "3d", "3e",
              "3f");

%musicop = (0xd2 => "Voice Volume", 0xd3 => "Voice Volume Fade",
	    0xd4 => "Pan", 0xd5 => "Pan Fade", 0xd6 => "Portamento",
            0xd7 => "Vibrato", 0xd8 => "Vibrato Off",
	    0xd9 => "Tremolo", 0xda => "Tremolo Off",
	    0xdb => "Pan Vibrato", 0xdc => "Pan Vibrato Off",
            0xdd => "Noise Freq", 0xde => "Noise On", 0xdf => "Noise Off",
            0xe0 => "Pitchmod On", 0xe1 => "Pitchmod Off",
            0xe2 => "Echo On", 0xe3 => "Echo Off", 0xe4 => "Set Octave",
            0xe5 => "Inc Octave", 0xe6 => "Dec Octave",
            0xe7 => "Set Transpose", 0xe8 => "Change Transpose",
	    0xe9 => "Detune", 0xea => "Patch Change", 0xeb => "ADSR Attack",
            0xec => "ADSR Decay", 0xed => "ADSR Sustain",
            0xee => "ADSR Release", 0xef => "Default ADSR",
            0xf0 => "Begin Repeat", 0xf1 => "End Repeat", 0xf2 => "Halt",
            0xf3 => "Tempo", 0xf4 => "Tempo Fade", 0xf5 => "nop",
	    0xf6 => "nop", 0xf7 => "nop", 0xf8 => "Master Volume",
            0xf9 => "Conditional Goto", 0xfa => "Goto",
            0xfb => "Goto if \$C4",
            0xfc => "Reset Conditional Goto Counter",
            0xfd => "Ignore Master Volume", 0xfe => "Halt", 0xff => "Halt");

@notes = ("C ", "C#", "D ", "D#", "E ", "F ",
          "F#", "G ", "G#", "A ", "A#", "B ");

#@patchmap  = (    0,  48,  46,   0,   0,  19,  56,  73, 13,  34, 47, 43,
#		-40, -36, -38, -35, -49, -42, 113, -69, 85, 117, 78); 

#@transpose = (  0, +1, +1,  0,  +1, +2,   0, +1,  0,  -2, -1, -1,
#		0,  0,  0,  0,   0,  0,   0,  0,  0,  +1,  0);

if (@ARGV) {
  foreach (@ARGV) {
    $dosong[hex $_] = 1;
  }
} else {
  @dosong = (1) x 0x40;
}  

@songptrs = &read3ptrs($base + 0x3d39, 0x40);
@sampptrs = &read3ptrs($base + 0x3df9, 0x21);
#print STDERR join("\n", map {o2s($_)} @sampptrs),"\n";

# get SPC data - music ops lengths, pitch table, duration table
seek ROM, $spcbase + 0x1484, 0;
read ROM, $buf, 46;
@musoplen = unpack "C46", $buf;

seek ROM, $spcbase + 0x14f2, 0;
read ROM, $buf, 26;
@pitchtbl = unpack "v13", $buf;

seek ROM, $spcbase + 0x152c, 0;
read ROM, $buf, 15;
@durtbl = unpack "C15", $buf;
#print STDERR "len:\n", join("\n", map {sprintf "%02x:%02x", ($loop++) + 0xc4, $_} @musoplen), "\n";
#print STDERR "dur: ", join(",", map {sprintf "%02x", $_} @durtbl), "\n";

$data2c_len = &read2ptrs(&s2o('C3/2137'), 1);
read ROM, $data2c, $data2c_len;
open OUT, "> txt/data2C" or warn;
for ($p=0; $p < 0x400; $p += 4) {
  my ($e9addr, $eaaddr) = unpack "v2", substr($data2c, $p, 4);
  printf OUT "A %02x: %04x    B %02x: %04x\n",
    $p/4, $e9addr, $p/4, $eaaddr;
}
print OUT "\n";
for ($p = 0x400; $p < $data2c_len; $p++) {
      printf OUT "      %04X: ", $p + 0x2c00;
      my $cmd = ord(substr($data2c, $p, 1));
      printf OUT "%02X", $cmd;
      if ($cmd < 0xd2) {
        # note
        print OUT "   " x 4;  # pad out
        my ($note, $dur) = (int($cmd/15), $cmd % 15);
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
        my $oplen = $musoplen[$cmd - 0xd2];
        print OUT map {sprintf " %02X",$_}
                unpack("C$oplen", substr($data2c, $p + 1, $oplen)) if $oplen;
        print OUT "   " x (4-$oplen);  # pad out
        print OUT "$musicop{$cmd}\n";
	print OUT "\n" if $cmd == 0xF2; # halt
        $p += $oplen;  # loop takes care of cmd
      }
}
close OUT;


for ($i = 0, $numdone=0; $i <= 0x40; $i++) {
  next unless $dosong[$i];

  my ($opus);

  printf STDERR "%02X ", $i;
  print STDERR "\n" if ++$numdone % 16 == 0;

  open OUT, sprintf "> txt/%02X-%s.txt", $i, $songtitle[$i];

  $opus = new MIDI::Opus ('format' => 1);

  # do song i
  my ($len) = &read2ptrs($songptrs[$i], 1);
  # read all song data
  read ROM, $song, $len;
  # grab instrument table
  my (@inst) = map { $_ == 0 ? () : ($_) }
                 &read2ptrs(&s2o('C3/3F22') + $i * 0x20, 0x10);
  my ($vaddroffset, $emptyvoice) 
        = (unpack("v", $song), unpack("v", substr($song, 0x12, 2)));
#printf STDERR "voff=%04x, ev=%04x\n", $vaddroffset, $emptyvoice;
  $vaddroffset = (0x11a14 - $vaddroffset) & 0xffff;
#printf STDERR "voff2=%04x\n", $vaddroffset;
  my (@vstart) = unpack "v8", substr($song, 2, 0x10);
  #my (@vstart2) = unpack "v8", substr($song, 0x14, 0x10);
#print STDERR "vs1: ", join(",", map {sprintf "%04x", $_} @vstart), "\n"; 
#print STDERR "vs2: ", join(",", map {sprintf "%04x", $_} @vstart2), "\n"; 
  @vstart = map {$_ == $emptyvoice ? 0 : ($_ + $vaddroffset) & 0xffff} @vstart;
  #@vstart2 = map {$_ == $emptyvoice ? 0 : ($_ + $vaddroffset) & 0xffff} @vstart2;
#print STDERR "vs1b: ", join(",", map {sprintf "%04x", $_} @vstart), "\n"; 
#print STDERR "vs2b: ", join(",", map {sprintf "%04x", $_} @vstart2), "\n"; 

  printf OUT "Song %02X - %s:\n", $i, $songtitle[$i];
  printf OUT "  Start %s  Length %04X\n", o2s($songptrs[$i]), $len;
  print OUT  "  Instruments:";
  for ($j = 0; $j < @inst; $j++) {
    printf OUT " %02X", $inst[$j];
  }
  print OUT "\n  Voice start addresses:\n   ";
  for ($j = 0; $j < 8; $j++) {
    printf OUT " %d:%04X", $j, $vstart[$j];
  }
  #print OUT "\n  Voice alternate start addresses:\n   ";
  #for ($j = 0; $j < 8; $j++) {
  #  printf OUT " %d:%04X", $j, $vstart2[$j];
  #}
  print OUT "\n";

  for ($v = 0; $v < 8; $v++) {
    next if $vstart[$v] < 0x100;

    my $track = new MIDI::Track;
    push @{$opus->tracks_r}, $track;
    $track->new_event('track_name', 0, "Voice $v");
    $track->new_event('control_change', 0, $v, 0, 0); # bank 0

    my $dtime = 0;  # time delta between MIDI events
    my $velocity = 64;
    my $octave = 4;
    my $perckey = 0;   # key to use for percussion
    my $t = 0;  # transpose octaves
    my (@e, @rpt); # event lists
    my $e = $e[0] = [];   # current event list
    my $ep = 0;       # idx of current event list
    my $master_rpt = 0;

    print OUT "\n" if $v > 0;
    print OUT "    Voice $v data:\n";

    for ($p = $vstart[$v] - 0x1a00; $p < $len; $p++) {
      last if grep {$_ == $p + 0x1a00 && $_ ne $vstart[$v]} @vstart;

      printf OUT "      %04X: ", $p + 0x1a00;
      my $cmd = ord(substr($song, $p, 1));
      printf OUT "%02X", $cmd;
      if ($cmd < 0xd2) {
        # note
        print OUT "   " x 4;  # pad out
        my ($note, $dur) = (int($cmd/15), $cmd % 15);
        if ($note < 12) {
          printf OUT "Note %s (%02d) Dur %02X\n",
            $notes[$note], $note, $durtbl[$dur];

	  my $mnote = 12 * ($octave + $t) + $note;
	  my $chan = $v;
	  if ($perckey) {
	    # substitute appropriate percussion key on chan 10
	    $chan = 9;  # 10 zero based
	    $mnote = $perckey;
	  }

          push @$e, ['note_on', $dtime, $chan, $mnote, $velocity];
	  push @$e, ['note_off', $durtbl[$dur] << 1, $chan, $mnote, 0];
	  $dtime = 0;
        } elsif ($note == 12) {
          printf OUT "Tie          Dur %02X\n", $durtbl[$dur];
	  my ($last_ev) = grep {$_->[0] eq 'note_off'}
				 reverse @$e;
	  $last_ev->[1] += $durtbl[$dur] << 1;
        } else {
          printf OUT "Rest         Dur %02X\n", $durtbl[$dur];
	  $dtime += $durtbl[$dur] << 1;
        }
      } else {
        # command
        my $oplen = $musoplen[$cmd - 0xd2];
        print OUT map {sprintf " %02X",$_}
                unpack("C$oplen", substr($song, $p + 1, $oplen)) if $oplen;
        print OUT "   " x (4-$oplen);  # pad out
        print OUT "$musicop{$cmd}";

	my ($op1, $op2, $op3) = map {ord} split //, substr($song, $p+1, 3);

        if ($cmd == 0xfa || $cmd == 0xfb) {
          my $newp = (($op1 + $op2 * 256) + $vaddroffset) & 0xffff;
          printf OUT ": %04X", $newp;
        } elsif ($cmd == 0xf9) {
          my $newp = (($op2 + $op3 * 256) + $vaddroffset) & 0xffff;
          printf OUT ": %04X at %02X", $newp, $op1;
        } elsif ($cmd == 0xd3 || $cmd == 0xd5 || $cmd == 0xf4) {
          # fades
          printf OUT ": steps=%02X value=%02X", $op1, $op2;
        } elsif ($cmd == 0xd6) {
          printf OUT ": steps=%02X halfsteps=%02X", $op1, $op2;
        } elsif ($cmd == 0xd7 || $cmd == 0xd9) {
          printf OUT ": delay=%02X steps=%02X depth=%02X", $op1, $op2, $op3;
        } elsif ($cmd == 0xdb) {
          printf OUT ": steps=%02X direction=%02X", $op2, $op1;
        } elsif ($cmd == 0xdd) {
          my (@nclk) = qw(0 16 21 25 31 42 50 63 84 100 125 167
                          200 250 333 400 500 667 800 1000 1300
                          1600 2000 2700 3200 4000 5300 6400 8000
                          10700 16000 32000);
          printf OUT ": freq=%s Hz", $nclk[$op1 & 0x1F];
        } elsif ($oplen == 1) {
          $op1++ if $cmd == 0xf0 && $op1 > 0;  # rpt begin
          printf OUT ": %02X", $op1;
        } 

        $p += $oplen;  # loop takes care of cmd
        print OUT "\n";
      }
    }
    $track->events(@$e);  # set track events from work array
  }
  #$opus->dump({dump_tracks => 1, flat => 1});
  #$opus->write_to_file(sprintf "mid/%02X - %s.mid", $i, $songtitle[$i]);
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

