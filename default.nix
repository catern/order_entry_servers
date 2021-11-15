{ pkgs ? import ./pinned.nix }:

with pkgs.python39Packages;
buildPythonPackage {
  name = "order_entry_servers";
  src = ./.;
  checkInputs = [
    mypy
  ];

  buildInputs = [
    cffi
  ];

  propagatedBuildInputs = [
    # rsyscall
    (rsyscall.overrideAttrs (old: { src = /home/sbaugh/.local/src/rsyscall/python; }))
  ];
}
