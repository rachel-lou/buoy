# Buoy firmware: Rust add-ons for Meshtastic (Heltec V3)

The buoy's Meshtastic node is a Heltec WiFi LoRa 32 V3 (ESP32-S3), the CP2102 USB device the Pi
talks to. This directory builds **stock Meshtastic firmware plus our own Rust code**, linked into
the same image and called from a small C++ Meshtastic module.

Right now the Rust side is a hello-world stub. It logs `hello from Rust` every 30 s and answers
the mesh text message `buoy ping` with `pong from Rust`. Nothing from the Python code has been
ported yet.

## Layout

```
firmware/
  meshtastic/              git submodule: upstream meshtastic/firmware, pinned to v2.8.1, unmodified
  buoy-rs/                 Rust crate -> libbuoy_rs.a (no_std on the ESP32, std for host tests)
    src/app.rs             logic: plain Rust, talks to the outside world only via the `Host` trait
    src/ffi.rs             the C ABI (extern "C" fns + #[repr(C)] types)
    src/esp.rs             ESP32 glue: Rust heap = ESP-IDF malloc, panic -> print + abort/reboot
    include/buoy_rs.h      C header, generated from ffi.rs by cbindgen on every cargo build
  module/Buoy/             C++ Meshtastic module that owns the Rust object and forwards to it
  variant/heltec_v3_buoy/  PlatformIO env `heltec-v3-buoy` (extends stock `heltec-v3`)
    cargo_build.py         pre-build hook: runs cargo, links libbuoy_rs.a
  setup.sh                 symlinks module/ and variant/ into meshtastic/ (re-runnable)
```

No Meshtastic source files are edited. `setup.sh` adds two symlinks inside the submodule:

- `variants/esp32s3/heltec_v3_buoy`: Meshtastic's `platformio.ini` already globs
  `variants/*/*/platformio.ini`, so the new env shows up automatically.
- `src/modules/optional/Buoy`: Meshtastic's `bin/optional-modules.py` automatically calls
  `setup<Name>()` for every `src/modules/optional/<Name>/<Name>.h`.

Upgrading Meshtastic therefore means checking out a newer tag in the submodule and rebuilding.

## How C++ and Rust talk

```
 Meshtastic (C++)                              buoy-rs (Rust)
 ----------------                              --------------
 BuoyModule ctor  -- buoy_new(BuoyHostApi) -->  Box<App>   (opaque `Buoy*` handed back)
 runOnce() timer  -- buoy_tick(buoy)       -->  App::tick() returns ms until next tick
 text packet rx   -- buoy_on_text(...)     -->  App::on_text()
                  <-- host.log / millis / send_text --   (function pointers + ctx = this)
```

The rules, which are also in the comments at the top of `src/ffi.rs`:

- **Rust-owned state is an opaque handle.** C++ never sees its layout. It must call `buoy_free`.
- **Rust → C++ goes through a struct of function pointers** (`BuoyHostApi`) passed at creation,
  not through global `extern "C"` symbols. `ctx` is the module's `this`. This keeps Rust
  independent of Meshtastic, and in tests a fake host stands in (see `app.rs` tests).
- **Strings are (ptr, len) UTF-8**, not NUL-terminated.
- **Plain data crosses as primitives or `#[repr(C)]` structs** declared in `ffi.rs`.
- **Never hand-edit `buoy_rs.h`.** Change `ffi.rs` and run `cargo build`.

To add a feature, put the logic in Rust (`app.rs` or new modules). Add an `extern "C"` entry point
in `ffi.rs` only if C++ needs to call it, and a callback in `BuoyHostApi` only if Rust needs
something from the firmware (radio, GPIO, I2C, …). C++ should stay thin glue.

## One-time machine setup (Linux)

Tested on Ubuntu 24.04, x86_64.

```bash
# 1. Serial port access for flashing (needs sudo). Log out/in afterwards.
sudo usermod -aG dialout $USER
curl -fsSL https://raw.githubusercontent.com/platformio/platformio-core/develop/platformio/assets/system/99-platformio-udev.rules \
  | sudo tee /etc/udev/rules.d/99-platformio-udev.rules
sudo udevadm control --reload-rules && sudo udevadm trigger

# 2. PlatformIO Core, PINNED to 6.1.19 (see "Gotchas").
#    Ubuntu's python3 lacks venv by default: either `sudo apt install python3.12-venv`
#    and use `python3 -m venv`, or use uv as below (no sudo).
curl -LsSf https://astral.sh/uv/install.sh | sh
~/.local/bin/uv venv --seed --python 3.12 ~/.platformio/penv
~/.local/bin/uv pip install --python ~/.platformio/penv/bin/python "platformio==6.1.19"
export PATH="$HOME/.platformio/penv/bin:$PATH"     # add to ~/.bashrc

# 3. Rust + Espressif's Xtensa Rust toolchain (the ESP32-S3 is Xtensa, not RISC-V).
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
source ~/.cargo/env
cargo install espup --locked
espup install --targets esp32s3 --name esp       # installs the `esp` toolchain (~2 GB)
```

`buoy-rs/rust-toolchain.toml` selects the `esp` toolchain automatically, so there's no need to
source `~/export-esp.sh`. That script is only needed for crates that compile C code.

## Build, flash, monitor

```bash
git submodule update --init --recursive
firmware/setup.sh                                  # after every fresh clone

cd firmware/meshtastic
pio run -e heltec-v3-buoy                          # build (first run ~5 min, downloads ESP-IDF)
pio run -e heltec-v3-buoy -t upload                # flash over USB (keeps node settings)
pio device monitor -b 115200                       # serial log
```

In the serial log you should see `[rust] buoy-rs: started` at boot and then
`[rust] hello from Rust: tick N at ... ms` every 30 s. Sending `buoy ping` from the Meshtastic
phone app (DM or channel) gets the reply `pong from Rust (tick N)`.

Stop the Pi's `buoy.service` (or unplug the node from the Pi) while flashing or monitoring, because only
one program can hold the serial port.

Rust unit tests run on the laptop, with no board needed:

```bash
cd firmware/buoy-rs
cargo +stable test --target x86_64-unknown-linux-gnu
```

## Gotchas we hit

- **PlatformIO must be 6.1.19.** Meshtastic's pinned ESP32 platform (pioarduino 55.03.31x)
  needs core ≥ 6.1.19 and pins SCons 4.8.1. Core 6.2.0 pins SCons 4.11.1. With 6.2.0 the two
  keep deleting each other's SCons mid-build, and the link fails with
  `ModuleNotFoundError: No module named 'SCons.Tool.FortranCommon'`. 6.1.18 is refused as too old.
- **The stock `heltec-v3` env still builds** with the symlinks in place. The module compiles
  to nothing unless `BUOY_RUST` is defined, and only `heltec-v3-buoy` defines it.
- The Rust lib is built with `build-std` (the Xtensa target is tier 3), `panic = "abort"`,
  LTO and `opt-level = "s"`. The current stub adds about 13 KB of flash.
- A Rust panic prints `*** Rust panic: ...` on the serial console, then the board reboots
  (ESP-IDF `abort()`).
