#!/usr/bin/perl

# pull song index mapping from seiken densetsu 3 english
# since the translators were kind enough to include a music
# test mode that maps songs from OSV order to actual SPC
# commands, let's reuse rather than reinvent.
#
# chris bongaarts 1 apr 2001

open ROM, "< seiken3e.smc" or die;

seek ROM, 0xf515 + 0x200, 0;
read ROM, $buf, 0xf59b-0xf515;
@dte = map { [unpack "C2", $_] } unpack("a2" x (length($buf) / 2), $buf);

read ROM, $buf, 0xf843 - 0xf59b;
@buf = unpack("C*", $buf);

for ($p=0; $p < @buf; $p++) {
  if ($buf[$p] >= 0xb0) {
    # song number
    #printf "%02x - %s\n", $buf[$p] - 0xb0, $name;
    $out[$buf[$p] - 0xb0] = $name;
    undef $name;
  } else {
    $name .= &undte($buf[$p]);
  }
}

print "\@songtitle = (\n\"", join(qq(",\n"), @out), "\");\n";

sub undte {
  # recursion in action.
  my $in = shift;
  if ($in >= 0x60) {
    return &undte($dte[$in - 0x60][0]) . &undte($dte[$in - 0x60][1]); 
  } else {
    return chr($in + 0x20);
  }
}
