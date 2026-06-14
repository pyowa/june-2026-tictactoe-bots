{
  description = "pyowa tic-tac-toe-event dev shell";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  inputs.flake-utils.url = "github:numtide/flake-utils";

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let pkgs = import nixpkgs { inherit system; };
      in {
        devShells.default = pkgs.mkShell {
          packages = with pkgs; [
            # Python side — matches the version compose uses for web/orchestrator.
            python313
            uv

            # Postgres client — psql, pg_isready, pg_dump. The server runs in compose.
            postgresql_16

            # Container runtime + CLI (replaces Docker Desktop).
            colima
            docker-client
            docker-compose

            # Kubernetes side.
            kind
            kubectl
            k9s

            # Day-to-day utilities.
            jq
            curl
          ];

          # Pin uv to Nix's Python so it doesn't silently download its own
          # interpreter — otherwise the flake stops being the single source
          # of truth for the Python version.
          shellHook = ''
            export UV_PYTHON=${pkgs.python313}/bin/python3.13
            export UV_PYTHON_DOWNLOADS=never
          '';
        };
      });
}
