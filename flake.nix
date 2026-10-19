{
  description = "smoked-salmon with locked Python dependencies and runtime tools";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

    smoked-salmon = {
      url = "github:smokin-salmon/smoked-salmon/0.20.0";
      flake = false;
    };

    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
    };

    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      smoked-salmon,
      pyproject-nix,
      uv2nix,
      pyproject-build-systems,
      ...
    }:
    let
      inherit (nixpkgs) lib;
      forAllSystems = lib.genAttrs [
        "x86_64-linux"
        "aarch64-linux"
      ];
      workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = smoked-salmon; };
      project = (builtins.fromTOML (builtins.readFile "${smoked-salmon}/pyproject.toml")).project;

      perSystem = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          pythonSet =
            (pkgs.callPackage pyproject-nix.build.packages {
              python = pkgs.python313;
            }).overrideScope
              (
                lib.composeManyExtensions [
                  pyproject-build-systems.overlays.wheel
                  (workspace.mkPyprojectOverlay { sourcePreference = "wheel"; })
                ]
              );
          venv = pythonSet.mkVirtualEnv "smoked-salmon-${project.version}-env" { salmon = [ ]; };
          runtimeTools = with pkgs; [
            curl
            flac
            gitMinimal
            lame
            mp3val
            sox
            rclone
            oxipng
          ];
          package = pkgs.stdenvNoCC.mkDerivation {
            pname = "smoked-salmon";
            version = project.version;
            dontUnpack = true;
            nativeBuildInputs = [ pkgs.makeWrapper ];
            installPhase = ''
              runHook preInstall
              mkdir -p "$out/bin" "$out/share/smoked-salmon"
              makeWrapper ${venv}/bin/salmon "$out/bin/salmon" \
                --prefix PATH : ${lib.makeBinPath runtimeTools}
              cp ${smoked-salmon}/src/salmon/data/config.default.toml \
                "$out/share/smoked-salmon/config.default.toml"
              runHook postInstall
            '';
            passthru = { inherit venv runtimeTools; };
            meta = {
              description = project.description;
              homepage = "https://github.com/smokin-salmon/smoked-salmon";
              license = lib.licenses.asl20;
              mainProgram = "salmon";
              platforms = [
                "x86_64-linux"
                "aarch64-linux"
              ];
            };
          };
        in
        {
          inherit package runtimeTools;
        }
      );
    in
    {
      packages = forAllSystems (system: {
        default = perSystem.${system}.package;
        smoked-salmon = perSystem.${system}.package;
      });

      apps = forAllSystems (system: {
        default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/salmon";
          meta.description = "Run smoked-salmon";
        };
      });

      checks = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          inherit (perSystem.${system}) package runtimeTools;
        in
        {
          smoke = pkgs.runCommand "smoked-salmon-smoke" { } ''
            ${package.venv}/bin/python ${./tests/smoke.py} \
              ${package} ${lib.makeBinPath runtimeTools} ${project.version}
            touch "$out"
          '';
        }
      );

      formatter = forAllSystems (system: nixpkgs.legacyPackages.${system}.nixfmt-tree);
    };
}
