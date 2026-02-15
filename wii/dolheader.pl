#!/usr/bin/perl
#
# dump wii/gamecube DOL (dolphin executable) header
# cab 2026-02-15

$filename = shift
  or die "usage: $0 <filename>\n";

open IN, "< $filename"
  or die "failed to open $filename: $!\n";

@text_offsets = &read_array(7);
@data_offsets = &read_array(11);
@text_load = &read_array(7);
@data_load = &read_array(11);
@text_size = &read_array(7);
@data_size = &read_array(11);

($bss_addr, $bss_size, $entry_point) = &read_array(3);

for ($i = 0; $i < 7; $i++) {
  if ($text_offsets[$i]) {
    printf "Text%d: offs %08x  size %08x  load addr %08x\n",
      $i, $text_offsets[$i], $text_size[$i], $text_load[$i];
  }
}

for ($i = 0; $i < 11; $i++) {
  if ($data_offsets[$i]) {
    printf "Data%d: offs %08x  size %08x  load addr %08x\n",
      $i, $data_offsets[$i], $data_size[$i], $data_load[$i];
  }
}

printf "Entry point: %08x  BSS addr %08x  BSS size %08x\n",
  $entry_point, $bss_addr, $bss_size;

sub read_array {
  my $len = shift;
  read IN, $buf, 4 * $len;
  return unpack "N$len", $buf;
}
