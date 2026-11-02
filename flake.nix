{
  description = "A native, real-valued mathematics library for the Nix evaluator";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/566acc07c54dc807f91625bb286cb9b321b5f42a";

  outputs = { self, nixpkgs }:
    let
      eachSystem = nixpkgs.lib.genAttrs [ "x86_64-linux" "aarch64-linux" ];
    in {
      packages = eachSystem (system:
        let pkgs = import nixpkgs { inherit system; };
        in rec {
          mathnix = pkgs.callPackage ./package.nix { };
          default = mathnix;
        });
      apps = eachSystem (system: {
        default = {
          type = "app";
          program = "${self.packages.${system}.mathnix}/bin/nix-math";
          meta.description = "Run the matching Nix evaluator with mathnix loaded";
        };
      });
      checks = eachSystem (system:
        let pkgs = import nixpkgs { inherit system; };
        in {
          integration = self.packages.${system}.mathnix;
          workflow = pkgs.runCommand "mathnix-workflow-check" {
            nativeBuildInputs = [ pkgs.actionlint ];
          } ''
            actionlint ${./.github/workflows/checks.yml}
            touch "$out"
          '';
        });
      devShells = eachSystem (system:
        let
          pkgs = import nixpkgs { inherit system; };
          package = self.packages.${system}.mathnix;
        in {
          default = pkgs.mkShell {
            inputsFrom = [ package ];
            packages = [ package.nixComponents.nix-cli pkgs.python3 ];
          };
        });
    };
}
