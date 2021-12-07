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
    (rsyscall.overrideAttrs (old: { src = (builtins.fetchGit {
      url = https://github.com/catern/rsyscall;
      ref = "for_order_entry_servers";
    }) + "/python"; }))
  ];
}
