#include "Buoy.h"
#ifdef BUOY_RUST
#include "BuoyModule.h"

#include "MeshService.h"
#include "NodeDB.h"
#include "configuration.h"

#include <Arduino.h>
#include <algorithm>
#include <cstring>

void setupBuoy()
{
    new BuoyModule();
}

BuoyModule::BuoyModule() : SinglePortModule("buoy", meshtastic_PortNum_TEXT_MESSAGE_APP), concurrency::OSThread("Buoy")
{
    BuoyHostApi host = {};
    host.ctx = this;
    host.log = hostLog;
    host.millis = hostMillis;
    host.send_text = hostSendText;
    buoy = buoy_new(host);
}

BuoyModule::~BuoyModule()
{
    buoy_free(buoy);
}

int32_t BuoyModule::runOnce()
{
    return buoy_tick(buoy);
}

ProcessMessage BuoyModule::handleReceived(const meshtastic_MeshPacket &mp)
{
    // Only DMs to us or broadcasts; ignore our own packets.
    if (mp.from == nodeDB->getNodeNum() || !(mp.to == nodeDB->getNodeNum() || isBroadcast(mp.to)))
        return ProcessMessage::CONTINUE;

    replyChannel = mp.channel;
    buoy_on_text(buoy, mp.from, mp.decoded.payload.bytes, mp.decoded.payload.size);
    // CONTINUE either way so the text still reaches the phone app / the Pi over serial.
    return ProcessMessage::CONTINUE;
}

void BuoyModule::hostLog(void *, uint8_t level, const uint8_t *msg, size_t len)
{
    const int n = static_cast<int>(len);
    const char *s = reinterpret_cast<const char *>(msg);
    switch (level) {
    case 0:
        LOG_DEBUG("[rust] %.*s", n, s);
        break;
    case 1:
        LOG_INFO("[rust] %.*s", n, s);
        break;
    case 2:
        LOG_WARN("[rust] %.*s", n, s);
        break;
    default:
        LOG_ERROR("[rust] %.*s", n, s);
        break;
    }
}

uint32_t BuoyModule::hostMillis(void *)
{
    return millis();
}

bool BuoyModule::hostSendText(void *ctx, uint32_t to, const uint8_t *msg, size_t len)
{
    auto *self = static_cast<BuoyModule *>(ctx);
    meshtastic_MeshPacket *p = self->allocDataPacket();
    if (!p)
        return false;
    p->to = to;
    p->channel = self->replyChannel;
    len = std::min(len, sizeof(p->decoded.payload.bytes));
    memcpy(p->decoded.payload.bytes, msg, len);
    p->decoded.payload.size = len;
    service->sendToMesh(p);
    return true;
}
#endif // BUOY_RUST
