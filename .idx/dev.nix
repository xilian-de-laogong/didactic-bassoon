{ pkgs, ... }: {
  channel = "stable-25.05";
  packages = [
    pkgs.gtk3
    pkgs.pango
    pkgs.cairo
    pkgs.gdk-pixbuf
    pkgs.atk
    pkgs.dbus
    pkgs.alsa-lib
    pkgs.fontconfig
    pkgs.freetype
    pkgs.playwright
    pkgs.playwright-driver
    pkgs.glib
    pkgs.libglibutil
    pkgs.nspr
    pkgs.nss
    pkgs.expat

    pkgs.xorg.libX11
    pkgs.xorg.libXcomposite
    pkgs.xorg.libXcursor
    pkgs.xorg.libXdamage
    pkgs.xorg.libXext
    pkgs.xorg.libXfixes
    pkgs.xorg.libXi
    pkgs.xorg.libXrandr
    pkgs.xorg.libXrender
    pkgs.xorg.libxcb

    pkgs.libgbm
    pkgs.libxkbcommon

    pkgs.chromium
    pkgs.chromedriver

    (pkgs.python312.withPackages (ps: with ps; [
      playwright
      flask
      requests
    ]))
  ];

  env = {};
  idx = {
    extensions = [
      "google.gemini-cli-vscode-ide-companion"
      "ms-python.python"
    ]; #

    previews = {
      enable = true;
      previews = {
        web = {
          command = [ "python" "main.py" ];
          env = { PORT = "$PORT"; };
          manager = "web";
        };
      };
    };
  };
}
