{ pkgs ? import <nixpkgs> {} }:

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
  ];
}
