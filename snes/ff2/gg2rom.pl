#!/usr/local/bin/perl

# convert game genie codes to bank/address/data

%ghex = ('D' => '0', 'F' => '1', '4' => '2', '7' => '3',
         '0' => '4', '9' => '5', '1' => '6', '5' => '7',
         '6' => '8', 'B' => '9', 'C' => 'A', '8' => 'B',
         'A' => 'C', '2' => 'D', '3' => 'E', 'E' => 'F');

while (<>) {
  chomp;
  s/-//;
  @code = map { $ghex{uc $_} } (split //);
#print "dehex: ", join ("", @code), "\n";
  $data = join("", splice(@code, 0, 2));
  @a = map { hex($_) } @code;
#print "addr vals: ", join(",", @a), "\n";
  $bank =
    (($a[2] & 0x3) << 6) |
    (($a[3] & 0xC) << 2) |
    (($a[4] & 0x3) << 2) |
    (($a[5] & 0xC) >> 2);
  $addr =
    ($a[0] << 12) |
    (($a[5] & 0x3) << 10) |
    (($a[2] & 0xC) << 6) |
    ($a[1] << 4) |
    (($a[3] & 0x3) << 2) |
    (($a[4] & 0xC) >> 2);

  printf "%02x/%04x = %s\n", $bank, $addr, $data;
}