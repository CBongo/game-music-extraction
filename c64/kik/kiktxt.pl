#!/usr/bin/perl

# kikstart (c64) music interpreter
# chris bongaarts 2023-04-28

@notenames = qw(C C# D D# E F F# G G# A A# B);

# menu music
open IN, "< kik1_1ad0.bin" or die;
read IN, $buf, 0x190 * 3;  # 400 bytes/voice
close IN;

@menu_bytes = unpack "C*", $buf;

# game music
open IN, "< kik1_4600.bin" or die;
read IN, $buf, 0xc0;  # 192 bytes, just 1 voice
close IN;

@bgm_bytes = unpack "C*", $buf;

for ($v=0; $v < 3; $v++) {
  print "=====  Menu - voice $v:\n";
  &process_voice(\@menu_bytes, $v * 0x190, 0x190);
  print "\n";
}
print "=====  BGM - voice 3:\n";
&process_voice(\@bgm_bytes, 0, 0xc0);

# the end.

sub process_voice {
  my ($b, $start, $len) = @_;

  for (my $i=$start; $i < $start + $len; $i++) {
    printf "  %02x ", $$b[$i];
    if ($$b[$i] == 0) {
      print "  Rest\n";
    } elsif ($$b[$i] == 1) {
      print "  Tie\n";
    } else {
      print "  ", &notename($$b[$i]), "\n";
    }
    print "\n" unless ($i+1) % 8;   # linefeed each measure
  }
}

sub notename {
  my $n = shift;
  $n -= 2;
  my $octave = int($n/12) + 2;
  my $notename = $notenames[$n % 12];
  return $notename . $octave;
}
