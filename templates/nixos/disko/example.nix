# Reference disko layout. NOT a Jinja template and NOT generated. This copy is
# documentation only -- the real per-host file lives in the INVENTORY repo at
# inventory/nixos/disko/<host>.nix (disk layout is operator/host config). render
# reads it from there and writes it verbatim into generated/nixos/disko/<host>.nix.
#
# To use: copy this to <inventory>/inventory/nixos/disko/<host>.nix, set the
# device + sizes for that host, then set `nixos_disko: true` on the host in
# inventory/dns.yaml. The host module imports it and the flake wires in
# disko.nixosModules.disko.
#
# Disk layout is operator territory (T-071/T-072): Medusa never derives a
# partition scheme. This single-disk GPT layout (ESP + ext4 root) suits a fresh
# Proxmox/KVM VM. Change `device` to match the guest's disk -- virtio shows up as
# /dev/vda, a SCSI controller as /dev/sda.
#
# WARNING: nixos-anywhere runs disko, which WIPES this disk. Fresh boxes only.
{
  disko.devices.disk.main = {
    type = "disk";
    device = "/dev/vda";
    content = {
      type = "gpt";
      partitions = {
        ESP = {
          size = "512M";
          type = "EF00";
          content = {
            type = "filesystem";
            format = "vfat";
            mountpoint = "/boot";
          };
        };
        root = {
          size = "100%";
          content = {
            type = "filesystem";
            format = "ext4";
            mountpoint = "/";
          };
        };
      };
    };
  };
}
