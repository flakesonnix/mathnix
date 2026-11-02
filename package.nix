{ lib
, stdenv
, cmake
, pkg-config
, makeWrapper
, nixVersions
, nixComponents ? nixVersions.nixComponents_2_34
, boost
, libblake3
, openssl
, libsodium
, brotli
, libcpuid
, curl
, libseccomp
, sqlite
, libgit2
, pcre2
, python3
}:

stdenv.mkDerivation {
  pname = "mathnix";
  version = "0.1.0";
  src = lib.cleanSourceWith {
    src = ./.;
    filter = path: type:
      !(builtins.elem (baseNameOf path) [ "build" "__pycache__" ])
      && lib.cleanSourceFilter path type;
  };

  nativeBuildInputs = [ cmake pkg-config makeWrapper ];
  # These also satisfy nix-expr's transitive pkg-config dependencies.
  buildInputs = [
    nixComponents.nix-expr boost libblake3 openssl libsodium brotli
    curl libseccomp sqlite libgit2 pcre2
  ]
    ++ lib.optionals stdenv.hostPlatform.isx86 [ libcpuid ];
  nativeCheckInputs = [ nixComponents.nix-cli python3 ];
  doCheck = true;
  doInstallCheck = true;

  preCheck = ''
    export HOME="$TMPDIR/home"
    mkdir -p "$HOME"
    export NIX_STATE_DIR="$TMPDIR/nix-state"
    export NIX_STORE_DIR="$TMPDIR/store"
    export NIX_CONFIG="experimental-features = nix-command flakes"
  '';

  postInstall = ''
    makeWrapper ${nixComponents.nix-cli}/bin/nix "$out/bin/nix-math" \
      --add-flags "--option plugin-files $out/lib/nix/plugins/mathnix.so"
  '';

  installCheckPhase = ''
    runHook preInstallCheck
    python3 ../tests/test_plugin.py \
      --nix ${nixComponents.nix-cli}/bin/nix-instantiate \
      --plugin "$out/lib/nix/plugins/mathnix.so" \
      --launcher "$out/bin/nix-math"
    runHook postInstallCheck
  '';

  passthru = { inherit nixComponents; };
  meta = {
    description = "Native mathematics builtins for Nix, with a matching evaluator launcher";
    license = lib.licenses.mit;
    platforms = lib.platforms.linux;
    mainProgram = "nix-math";
  };
}
