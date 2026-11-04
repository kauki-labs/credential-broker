{
  description = "credential-broker";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { nixpkgs, ... }:
    let
      forAllSystems =
        f:
        nixpkgs.lib.genAttrs [
          "x86_64-linux"
          "aarch64-linux"
          "x86_64-darwin"
          "aarch64-darwin"
        ] (system: f nixpkgs.legacyPackages.${system});
    in
    {
      # `nix run .#tests` from the repo root: lint the shell scripts, then run every offline suite.
      # The live suite (tests/acceptance_live.py) is not here: it needs the real key.
      apps = forAllSystems (pkgs: {
        tests = {
          type = "app";
          program = pkgs.lib.getExe (
            pkgs.writeShellApplication {
              name = "tests";
              runtimeInputs = with pkgs; [
                bash
                coreutils
                curl
                jq
                openssl
                python3
                shellcheck
              ];
              text = ''
                [ -f scripts/github-app-token ] || { echo "run from the credential-broker repo root" >&2; exit 2; }
                shellcheck --severity=warning -x scripts/github-app-token scripts/github-app-webhook scripts/lib-app-jwt.sh
                python3 -B -W error -I -m unittest discover -s tests "$@"
              '';
            }
          );
        };
      });
    };
}
