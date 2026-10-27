#pragma once
#ifdef BUOY_RUST

#include "SinglePortModule.h"
#include "concurrency/OSThread.h"

#include "buoy_rs.h"

/**
 * Thin C++ shell around the Rust add-ons (firmware/buoy-rs).
 *
 * C++ owns everything Meshtastic-specific: the thread schedule, reading sensors, receiving and
 * sending mesh packets. Rust owns the logic. Each direction is one hop:
 *   C++ -> Rust: buoy_tick() / buoy_on_text() on the opaque Buoy handle.
 *   Rust -> C++: the BuoyHostApi callbacks below, which get `this` back as ctx.
 */
class BuoyModule : public SinglePortModule, private concurrency::OSThread
{
  public:
    BuoyModule();
    ~BuoyModule() override;

  protected:
    int32_t runOnce() override;
    ProcessMessage handleReceived(const meshtastic_MeshPacket &mp) override;

  private:
    Buoy *buoy = nullptr;
    uint8_t replyChannel = 0;

    static void hostLog(void *ctx, uint8_t level, const uint8_t *msg, size_t len);
    static uint32_t hostMillis(void *ctx);
    static bool hostSendText(void *ctx, uint32_t to, const uint8_t *msg, size_t len);
};
#endif // BUOY_RUST
