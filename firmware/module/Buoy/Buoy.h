#pragma once

// Picked up by Meshtastic's bin/optional-modules.py: any src/modules/optional/<Name>/<Name>.h that
// declares setup<Name>() is called from setupModules(). Only the heltec-v3-buoy env links the
// Rust library (and defines BUOY_RUST); every other env gets a no-op.
#ifdef BUOY_RUST
void setupBuoy();
#else
inline void setupBuoy() {}
#endif
