#!/usr/bin/perl
#
#  dumptmd.pl - dump TMD structure for Wii game/channel
#  cab 2026-02-14
#

%sig_types = (0x10001 => "RSA_2048",
              0x10000 => "RSA_4096");

%title_types = (0x00001 => "System",
                0x10000 => "Game",
                0x10001 => "Channel",
                0x10002 => "SystemChannel",
                0x10004 => "GameWithChannel",
                0x10005 => "DLC",
                0x10008 => "HiddenChannel");

%title_flags = (0x01 => "Default",
                 0x08 => "Data (DLC)",
                 0x20 => "Maybe WFS");

%regions = (0 => "JP",
            1 => "US",
            2 => "EU",
            3 => "Region Free",
            4 => "KR");

%content_types = (0x0001 => "Normal",
                  0x4001 => "DLC",
                  0x8001 => "Shared");
$filename = shift
  or die "usage: $0 <filename.tmd>\n";

open IN, "< $filename"
  or die "failed to open $filename: $!\n";

read IN, $buf, 4;
($sigtype) = unpack "N", $buf;

read IN, $buf, 0x100;
$sig = $buf;

read IN, $buf, 60;  # skip alignment padding

read IN, $buf, 64;
($issuer) = unpack "A*", $buf;

read IN, $buf, 4 + 16 + 4 + 6;
($version, $ca_crl_version, $signer_crl_version, $is_vWii,
 $sys_version_major, $sys_version_minor, $title_id,
 $title_type, $group_id, $x, $region)
  = unpack "C4N2Q>NA2n2", $buf;

$sys_version = sprintf "%d.%d", $sys_version_major, $sys_version_minor;

read IN, $buf, 16;
$ratings = $buf;

read IN, $buf, 12;  # skip reserved

read IN, $buf, 12;
$ipc_mask = $buf;

read IN, $buf, 18;  # skip reserved

read IN, $buf, 4 + 8;
($access_rights, $title_version, $num_contents,
  $boot_index, $minor_version)
   = unpack "Nn4", $buf;

for ($i = 0; $i < $num_contents; $i++) {
  read IN, $buf, 4 + 4 + 8;
  ($content_id[$i], $content_index[$i], $content_type[$i],
   $content_size[$i])
     = unpack "NnnQ>", $buf;

  read IN, $buf, 20;
  $content_hash[$i] = $buf;
}

printf "Signature type: %s (%x)\n", $sig_types{$sigtype}, $sigtype;
print "Issuer: $issuer\n";
printf "Version: %02x  CA CRL version: %02x  Signer CRL version: %02x\n",
  $version, $ca_crl_version, $signer_crl_version;
printf "Is vWii?: %02x  System version: %s  Title ID: %16x\n",
  $is_vWii, $sys_version, $title_id;
printf "Title type: %s (%08x)  Group ID: %s  Region: %s (%04x)\n",
  $title_types{$title_type}, $title_type, $group_id, $regions{$region}, $region;
printf "Access rights: %08x  Title version: %04x  Minor version: %04x\n",
  $access_rights, $title_version, $minor_version;
printf "Number of contents: %04x  Boot index: %04x\n",
  $num_contents, $boot_index;
for ($i = 0; $i < $num_contents; $i++) {
  printf "== Contents %04x:\n", $i;
  printf "==   ID:  %08x   Index: %04x\n",
    $content_id[$i], $content_index[$i];
  printf "==   Type: %s (%04x)  Size: %08x\n",
    $content_types{$content_type[$i]}, $content_type[$i], $content_size[$i];
  print "\n" unless $i == $num_contents - 1;
}

