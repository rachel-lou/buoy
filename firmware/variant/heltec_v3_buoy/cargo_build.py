# trunk-ignore-all(ruff/F821)
# PlatformIO pre-script: builds firmware/buoy-rs with cargo and links libbuoy_rs.a into the
# firmware. Runs on every `pio run`; cargo is a no-op when nothing changed.
import os
import shutil
import subprocess

Import("env")

variant_dir = os.path.realpath(os.path.join(env.subst("$PROJECT_DIR"), "variants", "esp32s3", "heltec_v3_buoy"))
crate_dir = os.path.normpath(os.path.join(variant_dir, "..", "..", "buoy-rs"))
target = "xtensa-esp32s3-none-elf"

cargo = shutil.which("cargo") or os.path.expanduser("~/.cargo/bin/cargo")
print(f"buoy-rs: cargo build --release ({crate_dir})")
result = subprocess.run([cargo, "build", "--release", "--target", target], cwd=crate_dir)
if result.returncode != 0:
    env.Exit(result.returncode)

env.Append(
    CPPPATH=[os.path.join(crate_dir, "include")],
    LIBPATH=[os.path.join(crate_dir, "target", target, "release")],
    LIBS=["buoy_rs"],
)
